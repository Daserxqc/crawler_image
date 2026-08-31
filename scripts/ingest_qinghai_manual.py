# -*- coding: utf-8 -*-
"""Archive + ingest Qinghai city/prefecture manual HTML from output/manual/qinghai/."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html, resolve_list_child_url
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "qinghai"
ARCHIVE = MANUAL
QH_SLUG_RE = re.compile(r"qinghai\.chinatax\.gov\.cn/(?P<slug>[a-z]+)/", re.I)
LDJSS_DETAIL_RE = re.compile(r"/ldjss/\d{4,6}/", re.I)

NO_APPT_CODES: frozenset[str] = frozenset()

SLUG_TO_CITY = {
    "glz": "果洛州",
    "hds": "海东市",
    "hbz": "海北州",
    "hnz": "海南州",
    "hxz": "海西州",
    "ysz": "玉树州",
    "xns": "西宁市",
    "kfq": "西宁经济技术开发区",
    "hbzz": "黄南州",
}

BUREAU_NAMES = {
    f"qinghai_abbr_{slug}": (
        f"国家税务总局{city}税务局"
        if city != "西宁经济技术开发区"
        else "国家税务总局西宁经济技术开发区税务局"
    )
    for slug, city in SLUG_TO_CITY.items()
}

CITY_NAME_TO_CODE = {
    city: f"qinghai_abbr_{slug}" for slug, city in SLUG_TO_CITY.items()
}
CITY_NAME_TO_CODE["西宁"] = "qinghai_abbr_xns"
CITY_NAME_TO_CODE["经济技术开发区"] = "qinghai_abbr_kfq"

REGISTRY_ENTRIES = [
    {
        "code": "qinghai_abbr_glz",
        "name": "国家税务总局果洛州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/glz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/glz/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/glz/gdtp/commonlistmore.shtml?channelId=efeb016c303349f4ae5e9cb595214ff6&code=rsrm",
    },
    {
        "code": "qinghai_abbr_hds",
        "name": "国家税务总局海东市税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/hds/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/hds/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/hds/gdtp/commonlistmore.shtml?channelId=da19be8a22b24056a8e90bfa87983a25&code=rsrm",
    },
    {
        "code": "qinghai_abbr_hbz",
        "name": "国家税务总局海北州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/hbz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/hbz/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/hbz/rsrm/xxgk_fdzd_list.shtml",
    },
    {
        "code": "qinghai_abbr_hnz",
        "name": "国家税务总局海南州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/hnz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/hnz/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/hnz/gdtp/commonlistmore.shtml?channelId=54902d59010d461da9d571fd449b6b65&code=rsrm",
    },
    {
        "code": "qinghai_abbr_hxz",
        "name": "国家税务总局海西州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/hxz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/hxz/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/hxz/gdtp/commonlistmore.shtml?channelId=de6b998bec2e4e1791d5f0c9a6a3c8a4&code=rsrm",
    },
    {
        "code": "qinghai_abbr_ysz",
        "name": "国家税务总局玉树州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/ysz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/ysz/ldjss/xxgk_zn.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/ysz/gdtp/commonlistmore.shtml?channelId=4fca98dc2ca4419594cbd0e7321609e8&code=rsrm",
    },
    {
        "code": "qinghai_abbr_xns",
        "name": "国家税务总局西宁市税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/xns/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/xns/gdtp/commonlistmore.shtml?channelId=bc1c9605f3d448958994861753ab102d&code=ldjss",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/xns/gdtp/commonlistmore.shtml?channelId=8f19244b4e5c4c9c92556e8b33574aee&code=rsrm",
    },
    {
        "code": "qinghai_abbr_kfq",
        "name": "国家税务总局西宁经济技术开发区税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/kfq/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/kfq/ldjss/xxgk_zn.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/kfq/gdtp/commonlistmore.shtml?channelId=37975b14e2434d688be2ed9fe12873e8&code=rsrm",
    },
    {
        "code": "qinghai_abbr_hbzz",
        "name": "国家税务总局黄南州税务局",
        "home_url": "http://qinghai.chinatax.gov.cn/hbzz/index.shtml",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/hbzz/ldjss/xxgk_fdzd_list.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/hbzz/gdtp/commonlistmore.shtml?channelId=5c270fd43988479b8ec88d4f918b23c1&code=rsrm",
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
    m = re.search(r'<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta name="url" content="([^"]+)"', html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _slug_from_html(html: str) -> str:
    m = re.search(r'<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)', html, re.I)
    if m:
        sm = QH_SLUG_RE.search(m.group(1))
        if sm:
            return sm.group("slug").lower()
    sm = QH_SLUG_RE.search(html)
    return sm.group("slug").lower() if sm else ""


def _code_from_slug(slug: str) -> str:
    if not slug:
        return ""
    return f"qinghai_abbr_{slug.lower()}"


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    slug = _slug_from_html(html)
    if slug:
        code = _code_from_slug(slug)
        return code, BUREAU_NAMES.get(code, f"国家税务总局{SLUG_TO_CITY.get(slug, slug)}税务局")

    for city, code in CITY_NAME_TO_CODE.items():
        if city in filename:
            return code, BUREAU_NAMES.get(code, f"国家税务总局{city}税务局")

    return "", ""


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename:
        return True
    url = _saved_url(html, "")
    if "code=rsrm" in url or "/rsrm/" in url:
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


def _clean_person_label(text: str) -> str:
    return re.sub(r"[\s\u3000\u2002\u2003]+", "", text or "").strip()


def _qinghai_leader_targets(html: str, hub_url: str) -> list[str]:
    """Qinghai leader list pages use /ldjss/YYYY/…shtml, not ldjj/ldjs."""
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select(".zfxxgk_zd2 a[href], #list a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or "commonlistmore" in href or not LDJSS_DETAIL_RE.search(href):
            continue
        url = resolve_list_child_url(hub_url, href)
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls


def _qinghai_leader_stubs(html: str, hub_url: str, bureau_code: str) -> list[LeaderDuty]:
    soup = BeautifulSoup(html, "html.parser")
    stubs: list[LeaderDuty] = []
    for anchor in soup.select(".zfxxgk_zd2 a[href], #list a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or "commonlistmore" in href or not LDJSS_DETAIL_RE.search(href):
            continue
        name = _clean_person_label(str(anchor.get("title") or anchor.get_text(" ", strip=True)))
        if not is_plausible_person_name(name):
            continue
        url = resolve_list_child_url(hub_url, href)
        stubs.append(
            LeaderDuty(
                person_name=name,
                source_url=url,
                bureau_code=bureau_code,
            )
        )
    return stubs


def _classify_files() -> dict[str, dict]:
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)

    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("qinghai_")}

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


def _parse_leaders(code: str, paths: list[Path], *, try_fetch: bool) -> tuple[LeaderCrawlResult, list[str]]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    detail_urls: list[str] = []
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(
            all_duties,
            parse_leader_intro(html, hub, code),
            _qinghai_leader_stubs(html, hub, code),
        )
        detail_urls.extend(_qinghai_leader_targets(html, hub))
        detail_urls.extend(leader_page_targets(html, hub))

    seen_urls: set[str] = set()
    unique_urls: list[str] = []
    for url in detail_urls:
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen_urls:
            continue
        if not re.search(r"/(?:ldjss|ldjj|ldjs|ldzl|art/\d+/)/", url, re.I):
            continue
        seen_urls.add(key)
        unique_urls.append(url)

    missing_bio = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
    ]

    if try_fetch and unique_urls:
        session = create_session()
        by_name = {d.person_name: d for d in all_duties}
        for url in unique_urls:
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
            "parent_code": "qinghai",
            "region": "青海省",
        }
        notes = [n for n in (merged.get("notes") or []) if n not in ("appointment_url_missing", "leader_url_missing")]
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
    logging.info("classified %s Qinghai cities", len(groups))

    report = {"cities": [], "leader_missing_bio": {}, "no_appt": sorted(NO_APPT_CODES)}

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

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/qinghai_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/qinghai_manual_leaders.json", lead_payload)

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
