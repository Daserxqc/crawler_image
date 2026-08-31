# -*- coding: utf-8 -*-
"""Archive + ingest Yunnan city manual HTML from output/manual/yunnan/."""

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

MANUAL = ROOT / "output" / "manual" / "yunnan"
ARCHIVE = MANUAL
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)

CITY_NAME_TO_CODE = {
    "昆明市": "yunnan_col3875",
    "昆明": "yunnan_col3875",
    "曲靖市": "yunnan_col3876",
    "昭通市": "yunnan_col3877",
    "楚雄州": "yunnan_col3878",
    "楚雄": "yunnan_col3878",
    "玉溪市": "yunnan_col3879",
    "红河州": "yunnan_col3880",
    "文山州": "yunnan_col3881",
    "文山": "yunnan_col3881",
    "普洱市": "yunnan_col3882",
    "普洱": "yunnan_col3882",
    "版纳州": "yunnan_col3883",
    "大理州": "yunnan_col3884",
    "大理白族自治州": "yunnan_col3884",
    "保山市": "yunnan_col3885",
    "德宏州": "yunnan_col3886",
    "丽江市": "yunnan_col3887",
    "怒江州": "yunnan_col3888",
    "迪庆州": "yunnan_col3889",
    "临沧市": "yunnan_col3890",
    "滇中新区": "yunnan_col3891",
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
    # Filename first — shared TRS chrome / col4 often points at 昆明 or a catalog id.
    for city, code in CITY_NAME_TO_CODE.items():
        if city in filename:
            bureau = f"国家税务总局{city}税务局"
            return code, bureau

    if "大理" in filename:
        return "yunnan_col3884", "国家税务总局大理州税务局"

    col4_name = ""
    m_name = re.search(r'id="col4_name">([^<]+)', html)
    if m_name:
        col4_name = m_name.group(1).strip()
    m_url = re.search(r'id="col4_url">([^<]+)', html)
    if m_url:
        cm = COL_CODE_RE.search(m_url.group(1))
        if cm:
            code = f"yunnan_col{cm.group(1)}"
            bureau = f"国家税务总局{col4_name}税务局" if col4_name else code
            return code, bureau
    return "", col4_name


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename:
        return True
    if "任免工作人员" in html:
        return True
    col1 = re.search(r'id="col1_name">([^<]+)', html)
    if col1 and any(k in col1.group(1) for k in ("人事任免", "法定主动公开")):
        return "任免" in html
    return False


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

    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("yunnan_")}

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


def _patch_registry(list_urls: dict[str, str]) -> int:
    if not list_urls:
        return 0
    registry = load_city_registry()
    updated = 0
    for entry in registry:
        code = entry.get("code") or ""
        url = list_urls.get(code)
        if not url:
            continue
        if entry.get("appointment_list_url") != url:
            entry["appointment_list_url"] = url
            notes = list(entry.get("notes") or [])
            for tag in ("appointment_url_missing",):
                if tag in notes:
                    notes.remove(tag)
            if "yunnan_manual_appt" not in notes:
                notes.append("yunnan_manual_appt")
            entry["notes"] = notes
            updated += 1
    if updated:
        save_city_registry(registry)
        reload_sites()
    return updated


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

    if try_fetch and missing_bio and paths:
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
                    final, detail = fetch_html(session, url, referer=hub, follow_meta_refresh=False)
                    for duty in parse_leader_intro(detail, final, code):
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
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument(
        "--clear-leaders",
        action="store_true",
        help="Delete existing leader_duties for classified codes before ingest",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    if args.codes:
        want = set(args.codes)
        groups = {k: v for k, v in groups.items() if k in want}
    logging.info("classified %s Yunnan cities", len(groups))

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

    reload_sites()
    if args.clear_leaders and groups:
        conn = connect(args.db)
        try:
            for code in groups:
                n = conn.execute(
                    "DELETE FROM leader_duties WHERE bureau_code=?", (code,)
                ).rowcount
                logging.info("cleared %s leaders for %s", n, code)
            conn.commit()
        finally:
            conn.close()

    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    appt_results = []
    lead_results = []
    list_urls: dict[str, str] = {}
    for code, meta in sorted(groups.items()):
        leaders = meta.get("archived_leaders") or []
        if leaders:
            lr, missing = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
            lead_results.append(lr)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio)", code, len(lr.leaders), len(missing))
        ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
        if ar.list_url:
            list_urls[code] = ar.list_url
        if ar.list_count or ar.notices:
            appt_results.append(ar)
        if ar.failed:
            report["appt_failed"][code] = ar.failed[:10]
        report.setdefault("appt_stats", {})[code] = {
            "list_count": ar.list_count,
            "notices": len(ar.notices),
            "events": len(ar.events),
            "failed": len(ar.failed),
        }
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
        dump_json("output/yunnan_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/yunnan_manual_leaders.json", lead_payload)

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

    patched = _patch_registry(list_urls)
    if patched:
        logging.info("patched %s registry appointment_list_url entries", patched)
    report["registry_patched"] = patched

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
