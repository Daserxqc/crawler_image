# -*- coding: utf-8 -*-
"""Archive + ingest Shanxi city manual HTML from output/manual/shanxi/.

Leader pages: ``国家税务总局…税务局.html`` (URL ``/xxgk/leader/{abbr}-{id}``).
Appointment lists: filename ends with ``人事.html`` (URL ``/son/list/…`` or ``/zfxxgk/…``).
运城 currently has appointment HTML only (no leader page in folder).
"""

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
from tax_platform.crawler.ingest_resume import ingest_and_checkpoint_city, should_skip_city
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.crawler.resume_state import load_resume_state
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect

MANUAL = ROOT / "output" / "manual" / "shanxi"
ARCHIVE = MANUAL
BASE = "https://shanxi.chinatax.gov.cn"
JOB_ID = "ingest_shanxi_manual"

# /xxgk/leader/ty-11401  or  /son/list/ty-11401-4187  or  /zfxxgk/dt-11402-2048
SLUG_RE = re.compile(
    r"shanxi\.chinatax\.gov\.cn/(?:xxgk/leader|son/list|zfxxgk)/"
    r"(?P<abbr>[a-z]+)-(?P<id>\d+)",
    re.I,
)
CITY_MARKERS = (
    "山西转型综合改革示范区",
    "示范区",
    "太原市",
    "大同市",
    "阳泉市",
    "长治市",
    "晋城市",
    "朔州市",
    "晋中市",
    "运城市",
    "忻州市",
    "临汾市",
    "吕梁市",
)

CITY_TO_CODE: dict[str, str] = {
    "太原市": "shanxi_son_ty_11401",
    "大同市": "shanxi_son_dt_11402",
    "阳泉市": "shanxi_son_yq_11403",
    "长治市": "shanxi_son_cz_11404",
    "晋城市": "shanxi_son_jc_11405",
    "朔州市": "shanxi_son_sz_11406",
    "示范区": "shanxi_son_sf_11407",
    "山西转型综合改革示范区": "shanxi_son_sf_11407",
    "忻州市": "shanxi_son_xz_11422",
    "吕梁市": "shanxi_son_ll_11423",
    "晋中市": "shanxi_son_jz_11424",
    "临汾市": "shanxi_son_lf_11426",
    "运城市": "shanxi_son_yc_11427",
}

NO_APPT_CODES: frozenset[str] = frozenset()


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    best: tuple[int, str] | None = None
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = len(re.findall(r"[\u4e00-\u9fa5]", text)) - text.count("\ufffd") * 50
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best else raw.decode("utf-8", errors="replace")


def _saved_url(html: str, fallback: str = "") -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _code_from_url(url: str) -> str:
    m = SLUG_RE.search(url or "")
    if not m:
        return ""
    return f"shanxi_son_{m.group('abbr').lower()}_{m.group('id')}"


def _code_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"人事$", "", stem)
    for city in sorted(CITY_MARKERS, key=len, reverse=True):
        if city in stem:
            return CITY_TO_CODE.get(city, "")
    return ""


def _is_appt_file(filename: str) -> bool:
    return Path(filename).stem.endswith("人事")


def _duty_richness(duty: LeaderDuty) -> tuple[int, int, int]:
    deps = duty.departments_raw or []
    return (len(deps), 1 if (duty.duty_summary or "").strip() else 0, len(duty.title_raw or ""))


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

    registry = {
        e["code"]: e
        for e in load_city_registry()
        if str(e.get("parent_code", "")) == "shanxi"
    }

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            saved = _saved_url(html, "")
            code = _code_from_url(saved) or _code_from_filename(path.name)
            if not code:
                logging.warning("skip unclassified %s", path.name)
                continue
            reg = registry.get(code, {})
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "name": reg.get("name") or f"国家税务总局{code}税务局",
                    "leader_files": [],
                    "appt_index_html": None,
                    "leader_url": reg.get("leader_intro_url", ""),
                    "appt_url": reg.get("appointment_list_url", ""),
                    "home_url": reg.get("home_url") or BASE,
                },
            )
            if _is_appt_file(path.name):
                bucket["appt_index_html"] = path
                if saved:
                    bucket["appt_url"] = saved
            else:
                bucket["leader_files"].append(path)
                if saved:
                    bucket["leader_url"] = saved
            if reg.get("name"):
                bucket["name"] = reg["name"]

    return groups


def _registry_entries(groups: dict[str, dict]) -> list[dict]:
    entries = []
    for code, meta in sorted(groups.items()):
        entries.append(
            {
                "code": code,
                "name": meta["name"],
                "home_url": meta.get("home_url") or "",
                "leader_intro_url": meta.get("leader_url") or "",
                "appointment_list_url": meta.get("appt_url") or "",
                "level": "city",
                "parent_code": "shanxi",
                "region": "山西省",
                "notes": ["shanxi_manual"],
            }
        )
    return entries


def _patch_registry(groups: dict[str, dict]) -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for entry in _registry_entries(groups):
        code = entry["code"]
        merged = {**by_code.get(code, {}), **entry}
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
    if meta.get("appt_index_html"):
        appt_archived = dest / "appt_list.html"
        src = meta["appt_index_html"]
        if src.resolve() != appt_archived.resolve():
            shutil.copy2(src, appt_archived)
        manifest["files"].append("appt_list.html")

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_files") or [])
        if meta.get("appt_index_html"):
            paths.add(meta["appt_index_html"])
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


def _parse_leaders(code: str, paths: list[Path]) -> tuple[LeaderCrawlResult, list[str]]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))
    missing = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
    ]
    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(paths), leaders=all_duties),
        missing,
    )


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    path = meta.get("archived_appt_list")
    if path is None or not path.exists():
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    html = _read_html(path)
    list_url = _saved_url(html, meta.get("appt_url") or f"file:///{path.as_posix()}")
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
                time.sleep(0.25)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(
                        session,
                        item.source_url,
                        referer=list_url,
                        follow_meta_refresh=False,
                        allow_browser=True,
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
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--codes", nargs="+", metavar="CODE")
    args = parser.parse_args()

    only_codes = frozenset(args.codes) if args.codes else None

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    if only_codes:
        groups = {k: v for k, v in groups.items() if k in only_codes}
    logging.info("classified %s Shanxi cities", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report: dict = {
        "cities": [],
        "stats": {},
        "leader_missing_bio": {},
        "appt_failed": {},
        "no_leader": [],
        "no_appt": sorted(NO_APPT_CODES),
    }

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": bool(meta.get("appt_index_html")),
            }
        )
        if not meta.get("leader_files"):
            report["no_leader"].append(code)

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    resume_state = load_resume_state()
    appt_results = []
    lead_results = []
    for code, meta in sorted(groups.items()):
        if should_skip_city(resume_state, JOB_ID, code, resume=args.resume):
            continue
        leaders = meta.get("archived_leaders") or []
        lead_n = appt_list_n = appt_events_n = 0
        lr = None
        ar = None
        if leaders:
            lr, missing = _parse_leaders(code, leaders)
            lead_results.append(lr)
            lead_n = len(lr.leaders)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio)", code, lead_n, len(missing))
        if code not in NO_APPT_CODES and meta.get("archived_appt_list"):
            ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            appt_list_n = ar.list_count
            appt_events_n = len(ar.events)
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
        city_stats = {
            "name": meta["name"],
            "leaders": lead_n,
            "appt_list": appt_list_n,
            "appt_events": appt_events_n,
        }
        report["stats"][code] = city_stats
        ingest_and_checkpoint_city(
            code,
            lr,
            ar,
            db=args.db,
            job_id=JOB_ID,
            resume_state=resume_state,
            stats=city_stats,
        )

    if appt_results:
        dump_json("output/shanxi_manual_appointments.json", appointments_payload(appt_results))
    if lead_results:
        dump_json("output/shanxi_manual_leaders.json", leaders_payload(lead_results))

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
