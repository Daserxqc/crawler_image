# -*- coding: utf-8 -*-
"""Archive + ingest Zhejiang city manual HTML from output/manual/zhejiang/."""

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

MANUAL = ROOT / "output" / "manual" / "zhejiang"
ARCHIVE = MANUAL
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)
ART_COL_RE = re.compile(r"/art/\d+/\d+/\d+/art_(\d+)_", re.I)

NO_APPT_CODES: frozenset[str] = frozenset()

SLUG_TO_CODE = {
    "hangzhou": "zhejiang_col12641",
    "wenzhou": "zhejiang_col12642",
    "huzhou": "zhejiang_col12638",
    "shaoxing": "zhejiang_col12640",
    "jiaxing": "zhejiang_col12639",
    "jinhua": "zhejiang_col12637",
    "quzhou": "zhejiang_col12636",
    "zhoushan": "zhejiang_col12634",
    "taizhou": "zhejiang_col12635",
    "lishui": "zhejiang_col12633",
}

LEADER_ART_COL_TO_CODE = {
    "11743": "zhejiang_col12641",
    "11744": "zhejiang_col12642",
    "11745": "zhejiang_col12640",
    "11746": "zhejiang_col12639",
    "11747": "zhejiang_col12638",
    "11748": "zhejiang_col12637",
    "11749": "zhejiang_col12636",
    "11750": "zhejiang_col12635",
    "11751": "zhejiang_col12634",
    "11752": "zhejiang_col12633",
}

CITY_NAME_TO_CODE = {
    "杭州市": "zhejiang_col12641",
    "温州市": "zhejiang_col12642",
    "湖州市": "zhejiang_col12638",
    "绍兴市": "zhejiang_col12640",
    "嘉兴市": "zhejiang_col12639",
    "金华市": "zhejiang_col12637",
    "衢州市": "zhejiang_col12636",
    "舟山市": "zhejiang_col12634",
    "台州市": "zhejiang_col12635",
    "丽水市": "zhejiang_col12633",
    "宁波市": "zhejiang_ningbo",
}

BUREAU_NAMES = {
    "zhejiang_col12641": "国家税务总局杭州市税务局",
    "zhejiang_col12642": "国家税务总局温州市税务局",
    "zhejiang_col12638": "国家税务总局湖州市税务局",
    "zhejiang_col12640": "国家税务总局绍兴市税务局",
    "zhejiang_col12639": "国家税务总局嘉兴市税务局",
    "zhejiang_col12637": "国家税务总局金华市税务局",
    "zhejiang_col12636": "国家税务总局衢州市税务局",
    "zhejiang_col12634": "国家税务总局舟山市税务局",
    "zhejiang_col12635": "国家税务总局台州市税务局",
    "zhejiang_col12633": "国家税务总局丽水市税务局",
    "zhejiang_ningbo": "国家税务总局宁波市税务局",
}

REGISTRY_ENTRIES = [
    {
        "code": "zhejiang_col12633",
        "name": "国家税务总局丽水市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12633/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17925/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19984/index.html?vc_xxgkarea=11332500002645531G",
    },
    {
        "code": "zhejiang_col12635",
        "name": "国家税务总局台州市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12635/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17885/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19932/index.html?vc_xxgkarea=11331000002668223K",
    },
    {
        "code": "zhejiang_col12639",
        "name": "国家税务总局嘉兴市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12639/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17805/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19828/index.html?vc_xxgkarea=11330400MB1515866B",
    },
    {
        "code": "zhejiang_col12641",
        "name": "国家税务总局杭州市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12641/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17745/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19752/index.html?vc_xxgkarea=11330100MB1612172M",
    },
    {
        "code": "zhejiang_col12642",
        "name": "国家税务总局温州市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12642/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17765/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19776/index.html?vc_xxgkarea=113303000025196909",
    },
    {
        "code": "zhejiang_col12638",
        "name": "国家税务总局湖州市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12638/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17825/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19854/index.html?vc_xxgkarea=113305000025652086",
    },
    {
        "code": "zhejiang_col12640",
        "name": "国家税务总局绍兴市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12640/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17785/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19802/index.html?vc_xxgkarea=113306000025765052",
    },
    {
        "code": "zhejiang_col12634",
        "name": "国家税务总局舟山市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12634/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17905/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19958/index.html?vc_xxgkarea=113309000026350694",
    },
    {
        "code": "zhejiang_col12636",
        "name": "国家税务总局衢州市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12636/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17865/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19906/index.html?vc_xxgkarea=11330800002618488T",
    },
    {
        "code": "zhejiang_col12637",
        "name": "国家税务总局金华市税务局",
        "home_url": "http://zhejiang.chinatax.gov.cn/col/col12637/index.html",
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col17845/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col19880/index.html?vc_xxgkarea=11330700002592732Y",
    },
    {
        "code": "zhejiang_ningbo",
        "name": "国家税务总局宁波市税务局",
        "home_url": "https://ningbo.chinatax.gov.cn/",
        "leader_intro_url": "https://ningbo.chinatax.gov.cn/zfxxgkml/jggk/ldjj/index.html",
        "appointment_list_url": "https://ningbo.chinatax.gov.cn/zfxxgkml/rsgl/rsrm/index.html",
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
    m = re.search(r'<meta name="server" content="([^"]+)"', html, re.I)
    if m:
        server = m.group(1).strip()
        if server.startswith("http"):
            return server
        if "ningbo.chinatax.gov.cn" in server:
            return f"https://{server.lstrip('/')}"
    return fallback


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    saved = _saved_url(html, "")
    if "ningbo.chinatax.gov.cn" in html or "ningbo.chinatax.gov.cn" in saved or "宁波市" in filename:
        return "zhejiang_ningbo", BUREAU_NAMES["zhejiang_ningbo"]

    col3_name = ""
    m = re.search(r'id="col3_name">([^<]+)', html)
    if m:
        col3_name = m.group(1).strip()
    m_url = re.search(r'id="col3_url">([^<]+)', html)
    if m_url:
        slug = m_url.group(1).strip().strip("/").split("/")[0]
        if slug in SLUG_TO_CODE:
            code = SLUG_TO_CODE[slug]
            return code, col3_name or BUREAU_NAMES[code]

    if saved:
        m_art = ART_COL_RE.search(saved)
        if m_art and m_art.group(1) in LEADER_ART_COL_TO_CODE:
            code = LEADER_ART_COL_TO_CODE[m_art.group(1)]
            return code, BUREAU_NAMES[code]

    for city, code in CITY_NAME_TO_CODE.items():
        if city in filename or (col3_name and city.replace("市", "") in col3_name):
            return code, BUREAU_NAMES.get(code, f"国家税务总局{city}税务局")

    for col in COL_CODE_RE.findall(html):
        if col in LEADER_ART_COL_TO_CODE:
            code = LEADER_ART_COL_TO_CODE[col]
            return code, BUREAU_NAMES[code]

    return "", col3_name


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename:
        return True
    if "政府信息公开" in filename and ("任免工作人员" in html or 'infotypeId" id="infotypeId" value="Z25"' in html):
        return True
    col1 = re.search(r'id="col1_name">([^<]+)', html)
    if col1 and "人事任免" in col1.group(1):
        return True
    saved = _saved_url(html, "")
    return "/rsrm/" in saved and "ldjj" not in saved


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

    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("zhejiang_")}

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
            "parent_code": "zhejiang",
            "region": "浙江省",
        }
        notes = list(merged.get("notes") or [])
        if "zhejiang_manual" not in notes:
            notes.append("zhejiang_manual")
        merged["notes"] = notes
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    reload_sites()
    return updated


def _bio_complete(duty: LeaderDuty) -> bool:
    return bool((duty.title_raw or "").strip()) and bool(
        (duty.duty_summary or "").strip() or (duty.departments_raw or [])
    )


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
        if is_plausible_person_name(d.person_name) and not _bio_complete(d)
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
                if not re.search(r"/art/\d+/", url) and "/ldjj/" not in url:
                    continue
                try:
                    time.sleep(0.25)
                    final, detail = fetch_html(session, url, referer=hub, follow_meta_refresh=False)
                    for duty in parse_leader_intro(detail, final, code):
                        if not is_plausible_person_name(duty.person_name):
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
            if is_plausible_person_name(d.person_name) and not _bio_complete(d)
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


def _prune_implausible_leaders(conn, codes: list[str]) -> int:
    from tax_platform.normalize.person import is_plausible_person_name

    removed = 0
    for code in codes:
        rows = conn.execute(
            "SELECT id, person_name FROM leader_duties WHERE bureau_code = ?",
            (code,),
        ).fetchall()
        for row_id, person_name in rows:
            if is_plausible_person_name(person_name):
                continue
            conn.execute("DELETE FROM leader_duties WHERE id = ?", (row_id,))
            removed += 1
    return removed


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
    logging.info("classified %s Zhejiang cities", len(groups))

    report = {"cities": [], "leader_missing_bio": {}, "appt_failed": {}, "no_appt": sorted(NO_APPT_CODES)}

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
        dump_json("output/zhejiang_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/zhejiang_manual_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
        if lead_payload:
            pruned = _prune_implausible_leaders(conn, list(groups.keys()))
            if pruned:
                logging.info("pruned %s implausible leader rows", pruned)
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
