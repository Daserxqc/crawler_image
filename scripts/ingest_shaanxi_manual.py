# -*- coding: utf-8 -*-
"""Archive + ingest Shaanxi city manual HTML from output/manual/shaanxi/."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "shaanxi"
ARCHIVE = MANUAL
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)

CITY_NAME_TO_CODE = {
    "商洛市": "shaanxi_col124",
    "铜川市": "shaanxi_col2353",
    "安康市": "shaanxi_col2653",
    "渭南市": "shaanxi_col2856",
    "西安市": "shaanxi_col3557",
    "宝鸡市": "shaanxi_col399",
    "咸阳市": "shaanxi_col4113",
    "汉中市": "shaanxi_col4544",
    "榆林市": "shaanxi_col4843",
    "延安市": "shaanxi_col5534",
    "杨凌市": "shaanxi_col8185",
    "杨凌示范区": "shaanxi_col8185",
}


def _read_html(path: Path) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r'<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta name="url" content="([^"]+)"', html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    # Prefer city hub breadcrumb (col7_name = 「安康市税务局」 etc.)
    for key in ("col7_name", "col8_name", "col3_name"):
        m = re.search(rf'id="{key}">([^<]+)', html)
        if not m:
            continue
        label = m.group(1).strip()
        for city, code in CITY_NAME_TO_CODE.items():
            city_short = city.replace("市", "").replace("示范区", "")
            if city in label or (city_short and city_short in label and "陕西" not in label[:2]):
                bureau = f"国家税务总局{city}税务局".replace("杨凌示范区", "杨凌")
                return code, bureau

    for city, code in CITY_NAME_TO_CODE.items():
        if city in filename:
            bureau = f"国家税务总局{city}税务局".replace("杨凌示范区", "杨凌")
            return code, bureau

    # Avoid matching 西安 from provincial chrome when another city is present.
    for city, code in CITY_NAME_TO_CODE.items():
        if city == "西安市":
            continue
        if city in html:
            bureau = f"国家税务总局{city}税务局".replace("杨凌示范区", "杨凌")
            return code, bureau

    if "西安市" in filename or "西安市税务局" in html:
        return "shaanxi_col3557", "国家税务总局西安市税务局"
    return "", ""


def _is_appt_file(html: str, filename: str) -> bool:
    if any(k in filename for k in ("人事任免", "法定主动公开", "政府信息公开")):
        return True
    col1 = re.search(r'id="col1_name">([^<]+)', html)
    if col1 and any(k in col1.group(1) for k in ("人事任免", "法定主动公开")):
        return True
    return "任免工作人员" in html and "党委书记" not in filename


def _duty_richness(duty: LeaderDuty) -> tuple[int, int, int]:
    deps = duty.departments_raw or []
    return (
        len(deps),
        1 if (duty.duty_summary or "").strip() else 0,
        len(duty.title_raw or ""),
    )


def _merge_duties(*groups: list[LeaderDuty]) -> list[LeaderDuty]:
    by_name: dict[str, LeaderDuty] = {}
    for group in groups:
        for duty in group:
            if not is_plausible_person_name(duty.person_name):
                continue
            prev = by_name.get(duty.person_name)
            if prev is None or _duty_richness(duty) > _duty_richness(prev):
                by_name[duty.person_name] = duty
    return list(by_name.values())


def _classify_files() -> dict[str, dict]:
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)

    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("shaanxi_")}

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            code, bureau_name = _code_from_html(html, path.name)
            if not code:
                logging.warning("skip unclassified %s", path.name)
                continue
            reg = registry.get(code, {})
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "name": reg.get("name") or bureau_name,
                    "leader_files": [],
                    "appt_list_html": None,
                },
            )
            if _is_appt_file(html, path.name):
                bucket["appt_list_html"] = path
            else:
                bucket["leader_files"].append(path)
    return groups


def _patch_registry(groups: dict[str, dict]) -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for code, meta in groups.items():
        entry = by_code.get(code, {})
        leader_url = ""
        for path in meta.get("leader_files") or []:
            html = _read_html(path)
            leader_url = _saved_url(html, "")
            if leader_url:
                break
        appt_url = entry.get("appointment_list_url") or ""
        if meta.get("appt_list_html"):
            appt_url = _saved_url(_read_html(meta["appt_list_html"]), appt_url)
        merged = {
            **entry,
            "code": code,
            "name": meta.get("name") or entry.get("name") or "",
            "leader_intro_url": leader_url or entry.get("leader_intro_url") or "",
            "appointment_list_url": appt_url,
            "level": entry.get("level") or "city",
            "parent_code": "shaanxi",
            "region": entry.get("region") or "陕西省",
            "notes": list({*(entry.get("notes") or []), "shaanxi_manual"}),
        }
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    reload_sites()
    return updated


def _archive_group(code: str, meta: dict) -> Path:
    dest = ARCHIVE / code
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"code": code, "name": meta["name"], "files": []}

    leaders_dir = dest / "leaders"
    leaders_dir.mkdir(exist_ok=True)
    archived_leaders: list[Path] = []
    for i, src in enumerate(meta.get("leader_files") or []):
        target = leaders_dir / f"leader_{i}.html"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        archived_leaders.append(target)
        manifest["files"].append(f"leaders/leader_{i}.html")

    appt_archived = None
    if meta.get("appt_list_html"):
        appt_archived = dest / "appt_list.html"
        src = meta["appt_list_html"]
        if src.resolve() != appt_archived.resolve():
            shutil.copy2(src, appt_archived)
        manifest["files"].append("appt_list.html")

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_files") or [])
        if meta.get("appt_list_html"):
            paths.add(meta["appt_list_html"])
    for path in sorted(paths):
        if not path.exists() or path.parent != MANUAL:
            continue
        dest = source_dir / path.name
        if dest.resolve() != path.resolve():
            shutil.move(str(path), str(dest))
        asset_dir = MANUAL / f"{path.stem}_files"
        if asset_dir.is_dir():
            asset_dest = source_dir / asset_dir.name
            if asset_dest.exists():
                shutil.rmtree(asset_dest, ignore_errors=True)
            shutil.move(str(asset_dir), str(asset_dest))


def _parse_leaders(code: str, paths: list[Path], *, try_fetch: bool) -> tuple[LeaderCrawlResult, list[str]]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))

    missing_bio = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
    ]

    if try_fetch and paths:
        session = create_session()
        by_name = {d.person_name: d for d in all_duties}
        seen_urls: set[str] = set()
        for path in paths:
            html = _read_html(path)
            hub = _saved_url(html, f"file:///{path.as_posix()}")
            for url in leader_page_targets(html, hub):
                key = url.split("?", 1)[0].rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                try:
                    time.sleep(0.25)
                    final, detail = fetch_html(
                        session,
                        url,
                        referer=hub,
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    for duty in parse_leader_intro(detail, final, code):
                        if not is_plausible_person_name(duty.person_name):
                            continue
                        if duty.person_name in {"市局频道", "工作动态", "主要职责", "友情链接"}:
                            continue
                        prev = by_name.get(duty.person_name)
                        if prev is None or _duty_richness(duty) > _duty_richness(prev):
                            by_name[duty.person_name] = duty
                except Exception as exc:  # noqa: BLE001
                    logging.debug("leader fetch skip %s: %s", url, exc)
        session.close()
        all_duties = list(by_name.values())
        missing_bio = [
            d.person_name
            for d in all_duties
            if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
        ]

    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(paths), leaders=all_duties),
        missing_bio,
    )


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    path = meta.get("archived_appt_list")
    if path is None or not path.exists():
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    html = _read_html(path)
    list_url = _saved_url(html, f"file:///{path.as_posix()}")
    items = parse_appointment_list(html, list_url)
    notices = []
    events = []
    failed = []
    session = create_session() if try_fetch else None
    notices_dir = ARCHIVE / code / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)

    try:
        for item in items:
            if item.source_url in known:
                continue
            if not try_fetch or session is None:
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1].split("?")[0])[:80]
            cache_path = notices_dir / f"fetch_{slug}.html"
            try:
                time.sleep(0.2)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(
                        session, item.source_url, referer=list_url, follow_meta_refresh=False
                    )
                    cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": item.source_url, "error": str(exc)})
    finally:
        if session:
            session.close()

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--codes", nargs="+", metavar="CODE")
    parser.add_argument(
        "--clear-leaders",
        action="store_true",
        help="Delete existing leader_duties for selected cities before ingest",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    if args.codes:
        only = frozenset(args.codes)
        groups = {k: v for k, v in groups.items() if k in only}
    logging.info("classified %s Shaanxi cities", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report = {"cities": [], "leader_missing_bio": {}, "appt_failed": {}}

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": bool(meta.get("appt_list_html")),
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.clear_leaders:
        conn = connect(args.db)
        try:
            for code in groups:
                n = conn.execute("DELETE FROM leader_duties WHERE bureau_code=?", (code,)).rowcount
                logging.info("cleared %s leader rows for %s", n, code)
            conn.commit()
        finally:
            conn.close()

    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    appt_results = []
    lead_results = []
    for code, meta in sorted(groups.items()):
        leaders = meta.get("archived_leaders") or []
        if leaders:
            lr, missing = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
            lead_results.append(lr)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio)", code, len(lr.leaders), len(missing))
        ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
        if ar.list_count or ar.notices:
            appt_results.append(ar)
        if ar.failed:
            report["appt_failed"][code] = ar.failed[:10]
        logging.info(
            "APPT %s list=%s notices=%s events=%s failed=%s",
            code,
            ar.list_count,
            len(ar.notices),
            len(ar.events),
            len(ar.failed),
        )

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/shaanxi_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/shaanxi_manual_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
        if lead_payload:
            logging.info("ingest lead %s", ingest_leader_results(lead_payload, conn=conn))
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
    finally:
        conn.close()

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
