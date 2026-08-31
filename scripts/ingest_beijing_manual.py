# -*- coding: utf-8 -*-
"""Archive + ingest Beijing district manual HTML from output/manual/."""

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
from tax_platform.models.entities import NoticeMeta
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual"
ARCHIVE = MANUAL / "beijing"
BUREAU_RE = re.compile(
    r"(国家税务总局北京(?:市[\u4e00-\u9fa5]+|经济技术开发区|燕山地区)税务局)"
)
NO_APPT_CODES = frozenset({"beijing_yanshan", "beijing_jingkai"})

# 政府信息公开.html without prefix → 东城
APPT_FILE_DISTRICT = {
    "政府信息公开": "东城区",
}


def _read_html(path: Path) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _bureau_to_code(bureau: str) -> str:
    text = bureau.replace("国家税务总局", "").replace("北京市", "").replace("税务局", "")
    text = text.replace("经济技术开发区", "jingkai").replace("燕山地区", "yanshan")
    pmap = {
        "东城": "dongcheng",
        "西城": "xicheng",
        "朝阳": "chaoyang",
        "丰台": "fengtai",
        "石景山": "shijingshan",
        "海淀": "haidian",
        "门头沟": "mentougou",
        "房山": "fangshan",
        "通州": "tongzhou",
        "顺义": "shunyi",
        "昌平": "changping",
        "大兴": "daxing",
        "怀柔": "huairou",
        "平谷": "pinggu",
        "密云": "miyun",
        "延庆": "yanqing",
        "燕山": "yanshan",
        "jingkai": "jingkai",
    }
    for key, slug in pmap.items():
        if key in text:
            return f"beijing_{slug}"
    slug = re.sub(r"[^a-z0-9]", "", text.lower()) or "unknown"
    return f"beijing_{slug}"


def _extract_bureau(html: str) -> str:
    m = BUREAU_RE.search(html)
    return m.group(1) if m else ""


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r'<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)', html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _classify_files() -> dict[str, dict]:
    """Group manual HTML by bureau code."""
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = ARCHIVE / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)
    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            _classify_one_file(path, groups)
    return groups


def _classify_one_file(path: Path, groups: dict[str, dict]) -> None:
    html = _read_html(path)
    bureau = _extract_bureau(html)
    name = path.stem
    if not bureau and "任免" in name:
        bureau = name.split("任免")[0]
    if not bureau:
        for key, district in APPT_FILE_DISTRICT.items():
            if name == key or name.startswith(district.replace("区", "")):
                bureau = f"国家税务总局北京市{district}税务局"
                break
    if not bureau and "政府信息公开" in name:
        district = name.replace("政府信息公开", "")
        if district:
            bureau = f"国家税务总局北京市{district}税务局"
    if not bureau:
        if "燕山" in html:
            bureau = "国家税务总局北京市燕山地区税务局"
        elif "经济技术开发区" in html or "ld_kfq" in html:
            bureau = "国家税务总局北京经济技术开发区税务局"
    if not bureau:
        logging.warning("skip unclassified %s", path.name)
        return
    code = _bureau_to_code(bureau)
    bucket = groups.setdefault(
        code,
        {
            "code": code,
            "name": bureau,
            "region": "北京市",
            "parent_code": "beijing",
            "level": "district",
            "leader_html": None,
            "appt_list_html": None,
            "appt_detail_htmls": [],
        },
    )
    if any(x in name for x in ("政府信息公开",)) or (name.endswith("信息公开") and "任免" not in name):
        bucket["appt_list_html"] = path
    elif "任免工作人员" in name:
        bucket["appt_detail_htmls"].append(path)
    else:
        # person-name leader page
        if bucket["leader_html"] is None:
            bucket["leader_html"] = path
        else:
            logging.info("%s extra leader file %s (ignored)", code, path.name)


def _archive_group(code: str, meta: dict) -> Path:
    dest = ARCHIVE / code
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"code": code, "name": meta["name"], "files": []}
    archived_details: list[Path] = []

    def _copy(src: Path | None, dest_name: str) -> Path | None:
        if src is None or not src.exists():
            return None
        target = dest / dest_name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        manifest["files"].append(dest_name)
        return target

    _copy(meta.get("leader_html"), "leader.html")
    _copy(meta.get("appt_list_html"), "appt_list.html")
    notices_dir = dest / "notices"
    notices_dir.mkdir(exist_ok=True)
    for i, p in enumerate(meta.get("appt_detail_htmls") or []):
        ap = _copy(p, f"notices/detail_{i}.html")
        if ap:
            archived_details.append(ap)

    meta["archived_leader"] = dest / "leader.html"
    meta["archived_appt_list"] = dest / "appt_list.html"
    meta["archived_appt_details"] = archived_details

    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    """Move classified root manual HTML (+ _files dirs) into beijing/_source/."""
    source_dir = ARCHIVE / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        for key in ("leader_html", "appt_list_html"):
            p = meta.get(key)
            if p is not None:
                paths.add(p)
        for p in meta.get("appt_detail_htmls") or []:
            paths.add(p)
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


def _parse_leaders(code: str, path: Path, *, try_fetch: bool) -> tuple[LeaderCrawlResult, list[str]]:
    dest_dir = ARCHIVE / code
    fetched_path = dest_dir / "leader_fetched.html"
    html = _read_html(fetched_path) if fetched_path.exists() else _read_html(path)
    hub = _saved_url(html, f"file:///{path.as_posix()}")
    leaders = parse_leader_intro(html, hub, code)
    missing_bio = [
        d.person_name
        for d in leaders
        if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
    ]
    if try_fetch and missing_bio:
        session = create_session()
        targets = leader_page_targets(html, hub)
        by_name = {d.person_name: d for d in leaders}
        fetched_html = html
        for url in targets[:16]:
            if not any(m in url for m in ("ld_", "ldjj", "ldjs", "ldjianjie")):
                continue
            try:
                time.sleep(0.2)
                final, detail = fetch_html(session, url, referer=hub, follow_meta_refresh=False)
                if "ld_" in url and len(detail) > len(fetched_html):
                    fetched_html = detail
                for duty in parse_leader_intro(detail, final, code):
                    prev = by_name.get(duty.person_name)
                    if prev is None or (duty.title_raw and not prev.title_raw):
                        by_name[duty.person_name] = duty
            except Exception as exc:  # noqa: BLE001
                logging.debug("leader fetch skip %s: %s", url, exc)
        session.close()
        if fetched_html != html and fetched_path.parent.exists():
            fetched_path.write_text(fetched_html, encoding="utf-8")
        leaders = list(by_name.values())
        missing_bio = [
            d.person_name
            for d in leaders
            if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
        ]
    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=1, leaders=leaders),
        missing_bio,
    )


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> tuple[AppointmentCrawlResult, list[str]]:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0), []

    notices: list[NoticeMeta] = []
    events = []
    failed: list[dict[str, str]] = []
    need_manual: list[str] = []
    list_url = ""
    items = []

    session = create_session() if try_fetch else None
    try:
        if meta.get("archived_appt_list") and meta["archived_appt_list"].exists():
            path = meta["archived_appt_list"]
            html = _read_html(path)
            list_url = _saved_url(html, f"file:///{path.as_posix()}")
            items = parse_appointment_list(html, list_url)

        for ap in meta.get("archived_appt_details") or []:
            html = _read_html(ap)
            url = _saved_url(html, f"file:///{ap.as_posix()}")
            try:
                notice = parse_appointment_detail(html, url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": url, "error": str(exc)})

        for item in items:
            if item.source_url in known:
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1])[:80]
            cache_path = ARCHIVE / code / "notices" / f"fetch_{slug}.html"
            if cache_path.exists():
                detail_html = _read_html(cache_path)
                notice = parse_appointment_detail(detail_html, item.source_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
                continue
            if not try_fetch or session is None:
                need_manual.append(item.source_url)
                continue
            try:
                time.sleep(0.15)
                detail_url, detail_html = fetch_html(
                    session,
                    item.source_url,
                    referer=list_url,
                    follow_meta_refresh=False,
                )
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": item.source_url, "error": str(exc)})
                need_manual.append(item.title[:80])
    finally:
        if session:
            session.close()

    return (
        AppointmentCrawlResult(
            bureau=code,
            list_url=list_url,
            list_count=len(items),
            notices=notices,
            events=events,
            failed=failed,
        ),
        need_manual,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true", help="Try live fetch for leader/appt details")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--archive-source",
        action="store_true",
        help="Move classified root manual HTML into beijing/_source/ after ingest",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    logging.info("classified %s Beijing districts", len(groups))

    report = {"districts": [], "leader_missing_bio": {}, "appt_need_manual": {}}

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["districts"].append(
            {
                "code": code,
                "name": meta["name"],
                "has_leader": bool(meta.get("archived_leader") and meta["archived_leader"].exists()),
                "has_appt": code not in NO_APPT_CODES and (
                    bool(meta.get("archived_appt_list") and meta["archived_appt_list"].exists())
                    or bool(meta.get("archived_appt_details"))
                ),
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Update registry
    registry = [e for e in load_city_registry() if not str(e.get("code", "")).startswith("beijing_")]
    for code, meta in groups.items():
        entry = {
            "code": code,
            "name": meta["name"],
            "level": "district",
            "parent_code": "beijing",
            "home_url": "",
            "appointment_list_url": "",
            "leader_intro_url": "",
            "region": "北京市",
            "notes": ["beijing_manual"],
        }
        if meta.get("archived_leader") and meta["archived_leader"].exists():
            entry["leader_intro_url"] = f"file:///{meta['archived_leader'].as_posix()}"
        if code not in NO_APPT_CODES:
            if meta.get("archived_appt_list") and meta["archived_appt_list"].exists():
                entry["appointment_list_url"] = f"file:///{meta['archived_appt_list'].as_posix()}"
            elif meta.get("archived_appt_details"):
                entry["appointment_list_url"] = f"file:///{meta['archived_appt_details'][0].as_posix()}"
        registry.append(entry)
    save_city_registry(registry)
    reload_sites()

    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    appt_results = []
    lead_results = []
    for code, meta in sorted(groups.items()):
        if meta.get("archived_leader") and meta["archived_leader"].exists():
            lr, missing = _parse_leaders(code, meta["archived_leader"], try_fetch=args.try_fetch)
            lead_results.append(lr)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio)", code, len(lr.leaders), len(missing))
        if code not in NO_APPT_CODES:
            ar, need = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            if ar.list_count or ar.notices:
                appt_results.append(ar)
            if need:
                report["appt_need_manual"][code] = need[:30]
            logging.info(
                "APPT %s list=%s notices=%s events=%s",
                code,
                ar.list_count,
                len(ar.notices),
                len(ar.events),
            )

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/beijing_districts_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/beijing_districts_leaders.json", lead_payload)

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
    logging.info("wrote %s", report_path)
    if args.archive_source and not args.dry_run:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", ARCHIVE / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
