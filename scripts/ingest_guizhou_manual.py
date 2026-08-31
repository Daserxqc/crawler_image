# -*- coding: utf-8 -*-
"""Archive + ingest Guizhou city manual HTML from output/manual/guizhou/.

Guizhou city hubs live under ``/sjpd/{slug}/`` (市局频道). Leader pages may show
only the first bio with a sidebar of other leaders — use ``--try-fetch`` to follow
``…/ldjj/YYYYMM/t….html`` detail links (same pattern as Zhejiang).

贵安新区 has no 人事任免 column (NO_APPT).
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
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "guizhou"
ARCHIVE = MANUAL
SJPD_RE = re.compile(r"guizhou\.chinatax\.gov\.cn/sjpd/(?P<slug>[a-z0-9]+)/", re.I)
DETAIL_RE = re.compile(r"/t\d{8}_\d+\.html?(?:[?#]|$)", re.I)

# 贵安新区无人事任免栏目
NO_APPT_CODES: frozenset[str] = frozenset({"guizhou_sjpd_gaxqgwh"})

SLUG_TO_CITY = {
    "gys": "贵阳市",
    "zys": "遵义市",
    "lpss": "六盘水市",
    "ass": "安顺市",
    "bjs": "毕节市",
    "trs": "铜仁市",
    "qdnzzz": "黔东南州",
    "qnzzz": "黔南州",
    "qxnzzz": "黔西南州",
    "gaxqgwh": "贵安新区",
}

BUREAU_NAMES = {
    f"guizhou_sjpd_{slug}": f"国家税务总局贵州省{city}税务局"
    for slug, city in SLUG_TO_CITY.items()
}

CITY_NAME_TO_CODE = {
    "贵阳市": "guizhou_sjpd_gys",
    "贵阳": "guizhou_sjpd_gys",
    "遵义市": "guizhou_sjpd_zys",
    "遵义": "guizhou_sjpd_zys",
    "六盘水市": "guizhou_sjpd_lpss",
    "六盘水": "guizhou_sjpd_lpss",
    "安顺市": "guizhou_sjpd_ass",
    "安顺": "guizhou_sjpd_ass",
    "毕节市": "guizhou_sjpd_bjs",
    "毕节": "guizhou_sjpd_bjs",
    "铜仁市": "guizhou_sjpd_trs",
    "铜仁": "guizhou_sjpd_trs",
    "黔东南州": "guizhou_sjpd_qdnzzz",
    "黔东南": "guizhou_sjpd_qdnzzz",
    "黔南州": "guizhou_sjpd_qnzzz",
    "黔南": "guizhou_sjpd_qnzzz",
    "黔西南州": "guizhou_sjpd_qxnzzz",
    "黔西南": "guizhou_sjpd_qxnzzz",
    "贵安新区": "guizhou_sjpd_gaxqgwh",
    "贵安": "guizhou_sjpd_gaxqgwh",
}

REGISTRY_ENTRIES = [
    {
        "code": "guizhou_sjpd_gys",
        "name": "国家税务总局贵州省贵阳市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/gys/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/gys/xxgk_59635/gysswj_63598/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/gys/xxgk_59635/gysswj_63598/rsrm/",
    },
    {
        "code": "guizhou_sjpd_zys",
        "name": "国家税务总局贵州省遵义市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/zys/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/zys/xxgk_59686/zysswjj_63626/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/zys/xxgk_59686/zysswjj_63626/rsrm_5867368/",
    },
    {
        "code": "guizhou_sjpd_lpss",
        "name": "国家税务总局贵州省六盘水市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/lpss/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/lpss/xxgk_59878/lpsqswj_63935/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/lpss/xxgk_59878/lpsqswj_63935/rsrm_5867420/",
    },
    {
        "code": "guizhou_sjpd_ass",
        "name": "国家税务总局贵州省安顺市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/ass/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/ass/xxgk_59749/asqswj_63854/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/ass/xxgk_59749/asqswj_63854/rsrm_5867387/",
    },
    {
        "code": "guizhou_sjpd_bjs",
        "name": "国家税务总局贵州省毕节市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/bjs/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/bjs/xxgk_59788/bjqswj_63893/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/bjs/xxgk_59788/bjqswj_63893/rsrm_5867396/",
    },
    {
        "code": "guizhou_sjpd_trs",
        "name": "国家税务总局贵州省铜仁市税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/trs/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/trs/xxgk_59831/trswj_63913/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/trs/xxgk_59831/trswj_63913/rsrm_5867408/",
    },
    {
        "code": "guizhou_sjpd_qdnzzz",
        "name": "国家税务总局贵州省黔东南州税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/qdnzzz/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/qdnzzz/xxgk_59940/qdnzzzsswj/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/qdnzzz/xxgk_59940/qdnzzzsswj/rsrm_5867437/",
    },
    {
        "code": "guizhou_sjpd_qnzzz",
        "name": "国家税务总局贵州省黔南州税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/qnzzz/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/qnzzz/xxgk_59991/qnzzzsswj/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/qnzzz/xxgk_59991/qnzzzsswj/rsrm_5867456/",
    },
    {
        "code": "guizhou_sjpd_qxnzzz",
        "name": "国家税务总局贵州省黔西南州税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/qxnzzz/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/qxnzzz/xxgk_59901/qxnsswj/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/sjpd/qxnzzz/xxgk_59901/qxnsswj/rsrm_5867427/",
    },
    {
        "code": "guizhou_sjpd_gaxqgwh",
        "name": "国家税务总局贵州省贵安新区税务局",
        "home_url": "https://guizhou.chinatax.gov.cn/sjpd/gaxqgwh/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/sjpd/gaxqgwh/xxgk_60046/gaxqsswjj_63626/ldjj/",
        "appointment_list_url": None,
    },
]


def _read_html(path: Path) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta name="url" content="([^"]+)"', html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _slug_from_html(html: str) -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        sm = SJPD_RE.search(m.group(1))
        if sm:
            return sm.group("slug").lower()
    sm = SJPD_RE.search(html)
    return sm.group("slug").lower() if sm else ""


def _code_from_slug(slug: str) -> str:
    if not slug or slug not in SLUG_TO_CITY:
        return ""
    return f"guizhou_sjpd_{slug}"


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    slug = _slug_from_html(html)
    if slug:
        code = _code_from_slug(slug)
        if code:
            return code, BUREAU_NAMES.get(code, code)

    for city in sorted(CITY_NAME_TO_CODE, key=len, reverse=True):
        if city in filename:
            code = CITY_NAME_TO_CODE[city]
            return code, BUREAU_NAMES.get(code, code)

    return "", ""


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename:
        return True
    url = _saved_url(html, "")
    if "/rsrm" in url.lower():
        return True
    col1 = re.search(r'id="col1_name">([^<]+)', html)
    return bool(col1 and "人事任免" in col1.group(1))


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

    registry = {
        e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("guizhou_")
    }

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


def _is_leader_detail_url(url: str) -> bool:
    if re.search(r"/art/\d+/", url or ""):
        return True
    if "/ldjj/" in (url or "") and DETAIL_RE.search(url or ""):
        return True
    return False


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
                if not _is_leader_detail_url(url):
                    continue
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
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

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
    try:
        for item in items:
            if item.source_url in known:
                continue
            if not try_fetch or session is None:
                continue
            try:
                time.sleep(0.2)
                detail_url, detail_html = fetch_html(
                    session, item.source_url, referer=list_url, follow_meta_refresh=False
                )
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


def _patch_registry() -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for entry in REGISTRY_ENTRIES:
        code = entry["code"]
        merged = {
            **by_code.get(code, {}),
            **entry,
            "level": "city",
            "parent_code": "guizhou",
            "region": "贵州省",
        }
        notes = [
            n
            for n in (merged.get("notes") or [])
            if n not in ("appointment_url_missing", "leader_url_missing")
        ]
        if code in NO_APPT_CODES:
            merged["appointment_list_url"] = None
            if "no_appointment_column" not in notes:
                notes.append("no_appointment_column")
        merged["notes"] = notes
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    patched = _patch_registry()
    logging.info("patched %s registry entries", patched)

    groups = _classify_files()
    logging.info("classified %s Guizhou cities", len(groups))

    report: dict = {"cities": [], "leader_missing_bio": {}, "no_appt": sorted(NO_APPT_CODES)}

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": code not in NO_APPT_CODES and bool(meta.get("appt_list_html")),
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

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
        if code not in NO_APPT_CODES:
            ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            if ar.list_count or ar.notices:
                appt_results.append(ar)
            logging.info(
                "APPT %s list=%s notices=%s events=%s failed=%s",
                code,
                ar.list_count,
                len(ar.notices),
                len(ar.events),
                len(ar.failed),
            )
            if ar.failed:
                report.setdefault("appt_failed", {})[code] = ar.failed
        else:
            logging.info("APPT %s skipped (NO_APPT)", code)

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/guizhou_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/guizhou_manual_leaders.json", lead_payload)

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

    conn = connect(args.db)
    try:
        for city in report["cities"]:
            code = city["code"]
            city["leaders_db"] = conn.execute(
                "SELECT COUNT(*) FROM leader_duties WHERE bureau_code=?", (code,)
            ).fetchone()[0]
            city["appts_db"] = conn.execute(
                "SELECT COUNT(*) FROM appointment_events WHERE bureau_code=?", (code,)
            ).fetchone()[0]
            city["no_appt"] = code in NO_APPT_CODES
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
