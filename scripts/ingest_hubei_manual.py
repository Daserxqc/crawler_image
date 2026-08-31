# -*- coding: utf-8 -*-
"""Archive + ingest Hubei city manual HTML from output/manual/hubei/."""

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

from tax_platform.config.city_sites_io import load_city_registry
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

MANUAL = ROOT / "output" / "manual" / "hubei"
ARCHIVE = MANUAL
HB_SLUG_RE = re.compile(r"hubei\.chinatax\.gov\.cn/hbsw/(?P<slug>[a-z]+)/", re.I)
HB_RSRM_DETAIL_RE = re.compile(r"/hbsw/(?P<slug>[a-z]+)/xxgk/rsrm/\d+\.htm", re.I)

# Crawl returned list=0; registry has rsrm URL but column appears empty (not a parser bug).
LIKELY_EMPTY_APPT_CODES: frozenset[str] = frozenset(
    {
        "hubei_hbsw_enshi",
        "hubei_hbsw_ezhou",
        "hubei_hbsw_jingmen",
        "hubei_hbsw_shennongjia",
        "hubei_hbsw_shiyan",
        "hubei_hbsw_tianmen",
        "hubei_hbsw_xiangyang",
        "hubei_hbsw_xianning",
        "hubei_hbsw_xiantao",
    }
)

SLUG_TO_CITY = {
    "wuhan": "武汉市",
    "huanggang": "黄冈市",
    "huangshi": "黄石市",
    "jingzhou": "荆州市",
    "qianjiang": "潜江市",
    "shennongjia": "神农架林区",
    "shiyan": "十堰市",
    "suizhou": "随州市",
    "tianmen": "天门市",
    "xiangyang": "襄阳市",
    "xianning": "咸宁市",
    "xiantao": "仙桃市",
    "xiaogan": "孝感市",
    "yichang": "宜昌市",
    "enshi": "恩施州",
    "ezhou": "鄂州市",
    "jingmen": "荆门市",
}

BUREAU_NAMES = {
    f"hubei_hbsw_{slug}": f"国家税务总局{city}税务局"
    for slug, city in SLUG_TO_CITY.items()
}

CITY_NAME_TO_CODE = {city: f"hubei_hbsw_{slug}" for slug, city in SLUG_TO_CITY.items()}
CITY_NAME_TO_CODE.update(
    {
        "武汉": "hubei_hbsw_wuhan",
        "黄石": "hubei_hbsw_huangshi",
        "黄冈": "hubei_hbsw_huanggang",
        "荆州": "hubei_hbsw_jingzhou",
        "潜江": "hubei_hbsw_qianjiang",
        "神农架": "hubei_hbsw_shennongjia",
        "十堰": "hubei_hbsw_shiyan",
        "随州": "hubei_hbsw_suizhou",
        "天门": "hubei_hbsw_tianmen",
        "襄阳": "hubei_hbsw_xiangyang",
        "咸宁": "hubei_hbsw_xianning",
        "仙桃": "hubei_hbsw_xiantao",
        "孝感": "hubei_hbsw_xiaogan",
        "宜昌": "hubei_hbsw_yichang",
        "恩施": "hubei_hbsw_enshi",
        "鄂州": "hubei_hbsw_ezhou",
        "荆门": "hubei_hbsw_jingmen",
    }
)


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


def _slug_from_html(html: str) -> str:
    url = _saved_url(html, "")
    m = HB_SLUG_RE.search(url) or HB_SLUG_RE.search(html)
    if m:
        return m.group("slug").lower()
    m = HB_RSRM_DETAIL_RE.search(url) or HB_RSRM_DETAIL_RE.search(html)
    return m.group("slug").lower() if m else ""


def _code_from_slug(slug: str) -> str:
    if not slug:
        return ""
    return f"hubei_hbsw_{slug.lower()}"


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    slug = _slug_from_html(html)
    if slug:
        code = _code_from_slug(slug)
        return code, BUREAU_NAMES.get(code, f"国家税务总局{SLUG_TO_CITY.get(slug, slug)}税务局")

    for city in sorted(CITY_NAME_TO_CODE, key=len, reverse=True):
        if city in filename:
            code = CITY_NAME_TO_CODE[city]
            return code, BUREAU_NAMES.get(code, f"国家税务总局{city}税务局")

    return "", ""


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename or "任免" in filename:
        return True
    url = _saved_url(html, "")
    if "/rsrm/" in url and url.rstrip("/").endswith(".htm"):
        return True
    col1 = re.search(r'id="col1_name">([^<]+)', html)
    return bool(col1 and "人事任免" in col1.group(1))


def _is_appt_detail_url(url: str) -> bool:
    if re.search(r"/art/\d+/\d+/\d+/art_\d+_", url or "", re.I):
        return True
    return bool(HB_RSRM_DETAIL_RE.search(url or ""))


def _list_url_from_detail(url: str) -> str:
    m = HB_RSRM_DETAIL_RE.search(url or "")
    if not m:
        return ""
    slug = m.group("slug")
    return f"http://hubei.chinatax.gov.cn/hbsw/{slug}/xxgk/rsrm/index.html"


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

    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("hubei_hbsw_")}

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
                    "appt_detail_htmls": [],
                },
            )
            if _is_appt_file(html, path.name):
                url = _saved_url(html, "")
                if _is_appt_detail_url(url):
                    bucket["appt_detail_htmls"].append(path)
                else:
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

    archived_details: list[Path] = []
    if meta.get("appt_detail_htmls"):
        notices_dir = dest / "notices"
        notices_dir.mkdir(exist_ok=True)
        for i, src in enumerate(meta["appt_detail_htmls"]):
            target = notices_dir / f"manual_{i}.html"
            if src.resolve() != target.resolve():
                shutil.copy2(src, target)
            archived_details.append(target)
            manifest["files"].append(f"notices/manual_{i}.html")

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
    meta["archived_appt_details"] = archived_details
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
        paths.update(meta.get("appt_detail_htmls") or [])
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
                if not re.search(r"/art/\d+/", url):
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
    notices = []
    events = []
    failed = []
    list_url = ""
    items = []
    seen_notice_urls: set[str] = set()

    for ap in meta.get("archived_appt_details") or []:
        html = _read_html(ap)
        url = _saved_url(html, f"file:///{ap.as_posix()}")
        if not list_url:
            list_url = _list_url_from_detail(url) or url
        try:
            notice = parse_appointment_detail(html, url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
            seen_notice_urls.add(url)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": url, "error": str(exc)})

    path = meta.get("archived_appt_list")
    if path is not None and path.exists():
        html = _read_html(path)
        list_url = _saved_url(html, f"file:///{path.as_posix()}")
        items = parse_appointment_list(html, list_url)

    if try_fetch and not items and list_url and "/rsrm/" in list_url:
        session = create_session()
        try:
            time.sleep(0.2)
            final, list_html = fetch_html(session, list_url, follow_meta_refresh=False)
            list_url = final
            items = parse_appointment_list(list_html, list_url)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": list_url, "error": f"list fetch: {exc}"})
        finally:
            session.close()

    session = create_session() if try_fetch else None
    try:
        for item in items:
            if item.source_url in known or item.source_url in seen_notice_urls:
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
                seen_notice_urls.add(detail_url)
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": item.source_url, "error": str(exc)})
    finally:
        if session:
            session.close()

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items) if items else len(meta.get("archived_appt_details") or []),
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    logging.info("classified %s Hubei cities", len(groups))

    report = {
        "cities": [],
        "leader_missing_bio": {},
        "likely_empty_appt": sorted(LIKELY_EMPTY_APPT_CODES),
    }

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_files": len(meta.get("leader_files") or []),
                "appt_details": len(meta.get("appt_detail_htmls") or []),
                "has_appt_list": bool(meta.get("appt_list_html")),
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

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/hubei_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/hubei_manual_leaders.json", lead_payload)

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
