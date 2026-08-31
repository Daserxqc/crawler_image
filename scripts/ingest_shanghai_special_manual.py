# -*- coding: utf-8 -*-
"""Ingest Shanghai special bureaus (稽查局 / 税务分局) from output/manual/shanghai/."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import get_site, reload_sites
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.appointment_list import AppointmentListItem, parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

import requests
from datetime import date, datetime
from xml.etree import ElementTree as ET

MANUAL = ROOT / "output" / "manual" / "shanghai"
ARCHIVE = MANUAL / "_archive"

# filename stem fragment -> bureau code
BUREAU_KEYS: list[tuple[str, str]] = [
    ("第一稽查局", "dyjcj"),
    ("第二稽查局", "dejcj"),
    ("第三稽查局", "dsjcj"),
    ("第四稽查局", "dsijcj"),
    ("第五稽查局", "dwjcj"),
    ("第三税务分局", "swfj"),
    ("第四税务分局", "sswfj"),
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
        return m.group(1).rstrip("#")
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).rstrip("#")
    return fallback


def _code_from_name(name: str) -> str | None:
    for key, code in BUREAU_KEYS:
        if key in name:
            return code
    return None


def _classify() -> dict[str, dict[str, Path]]:
    found: dict[str, dict[str, Path]] = {}
    if not MANUAL.exists():
        return found
    for path in sorted(MANUAL.glob("*.html")):
        code = _code_from_name(path.stem)
        if not code:
            continue
        bucket = found.setdefault(code, {})
        if "领导简介" in path.stem or "领导介绍" in path.stem or "ldjj" in path.stem.lower():
            bucket["leader"] = path
        else:
            bucket["appt_hub"] = path
    return found


def _ingest_leaders(code: str, path: Path) -> LeaderCrawlResult:
    html = _read_html(path)
    hub = _saved_url(html, get_site(code).leader_intro_url or f"file:///{path.as_posix()}")
    leaders = parse_leader_intro(html, hub, code)
    return LeaderCrawlResult(bureau=code, hub_url=hub, page_count=1, leaders=leaders)


def _cdata_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return (node.text or "").strip()


def _parse_pub_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _items_from_xxgk_xml(list_url: str, *, session: requests.Session) -> list[AppointmentListItem]:
    """Shanghai 主动公开栏目列表常在 xxgk.xml / xxgk_N.xml，HTML 目录页可能不含条目。"""
    base = (list_url or "").rstrip("/")
    if not base.endswith("/rsrm"):
        # list_url may already be .../rsrm/ or a hub page; prefer configured rsrm folder
        if "/rsrm" in base:
            base = base.split("/rsrm")[0] + "/rsrm"
        else:
            return []
    base = base.replace("http://", "https://")
    items: list[AppointmentListItem] = []
    seen: set[str] = set()
    page = 0
    while page < 30:
        name = "xxgk.xml" if page == 0 else f"xxgk_{page}.xml"
        url = f"{base}/{name}"
        try:
            resp = session.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                    "Referer": list_url or base + "/",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
        except requests.RequestException:
            break
        if resp.status_code != 200 or b"<RECS" not in resp.content[:200]:
            break
        root = ET.fromstring(resp.content)
        recs = root.findall("REC")
        if not recs:
            break
        for rec in recs:
            title = _cdata_text(rec.find("TITLE"))
            href = _cdata_text(rec.find("URL")).replace("http://", "https://")
            pub = _parse_pub_date(_cdata_text(rec.find("DOCRELTIME")))
            if not title or not href or href in seen:
                continue
            seen.add(href)
            items.append(AppointmentListItem(title=title, source_url=href, published_on=pub))
        page_count = int(_cdata_text(root.find("PAGECOUNT")) or "1")
        page += 1
        if page >= page_count:
            break
    return items


def _ingest_appointments(
    code: str,
    path: Path | None,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    site = get_site(code)
    list_url = site.appointment_list_url or ""
    items: list[AppointmentListItem] = []
    if path and path.exists():
        html = _read_html(path)
        list_url = _saved_url(html, list_url or f"file:///{path.as_posix()}")
        items = parse_appointment_list(html, list_url)

    notices = []
    events = []
    failed: list[dict[str, str]] = []

    session = create_session() if try_fetch else None
    cache_dir = ARCHIVE / code / "notices"
    try:
        if not items and try_fetch and session is not None and site.appointment_list_url:
            xml_items = _items_from_xxgk_xml(site.appointment_list_url, session=session)
            if xml_items:
                logging.info("%s HTML list empty; loaded %s from xxgk.xml", code, len(xml_items))
                items = xml_items
                list_url = site.appointment_list_url

        for item in items:
            if item.source_url in known:
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1])[:80]
            cache_path = cache_dir / f"fetch_{slug}.html"
            if cache_path.exists():
                detail_html = _read_html(cache_path)
                try:
                    notice = parse_appointment_detail(detail_html, item.source_url, code)
                    notices.append(notice)
                    events.extend(extract_appointment_events(notice))
                except Exception as exc:  # noqa: BLE001
                    failed.append({"url": item.source_url, "error": str(exc)})
                continue
            if not try_fetch or session is None:
                failed.append({"url": item.source_url, "error": "need --try-fetch"})
                continue
            try:
                time.sleep(0.2)
                detail_url, detail_html = fetch_html(
                    session,
                    item.source_url,
                    referer=list_url or site.home_url,
                    follow_meta_refresh=False,
                )
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                # HTML list pages may 403; detail sometimes still works via plain GET
                try:
                    resp = session.get(
                        item.source_url,
                        timeout=25,
                        headers={
                            "User-Agent": session.headers.get("User-Agent", "Mozilla/5.0"),
                            "Referer": site.home_url,
                        },
                    )
                    if resp.status_code == 200 and len(resp.text) > 500:
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(resp.text, encoding="utf-8")
                        notice = parse_appointment_detail(resp.text, item.source_url, code)
                        notices.append(notice)
                        events.extend(extract_appointment_events(notice))
                        continue
                except Exception:  # noqa: BLE001
                    pass
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
    parser.add_argument("--try-fetch", action="store_true", help="Fetch appointment detail pages")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="", help="Comma-separated bureau codes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reload_sites()

    only = {c.strip() for c in args.only.split(",") if c.strip()}
    classified = _classify()
    if only:
        classified = {k: v for k, v in classified.items() if k in only}

    if not classified:
        logging.error("No matching HTML under %s", MANUAL)
        raise SystemExit(1)

    lead_results: list[LeaderCrawlResult] = []
    appt_results: list[AppointmentCrawlResult] = []
    report: dict[str, dict] = {}

    conn = connect(args.db)
    known = known_notice_urls(conn=conn)

    for code, meta in sorted(classified.items()):
        get_site(code)  # ensure registered
        entry: dict = {"code": code}
        if meta.get("leader"):
            lr = _ingest_leaders(code, meta["leader"])
            lead_results.append(lr)
            entry["leaders"] = len(lr.leaders)
            logging.info("%s leaders=%s from %s", code, len(lr.leaders), meta["leader"].name)
        ar = _ingest_appointments(
            code, meta.get("appt_hub"), known=known, try_fetch=args.try_fetch
        )
        appt_results.append(ar)
        entry["list_count"] = ar.list_count
        entry["notices"] = len(ar.notices)
        entry["events"] = len(ar.events)
        entry["failed"] = len(ar.failed)
        logging.info(
            "%s appt list=%s notices=%s events=%s failed=%s",
            code,
            ar.list_count,
            len(ar.notices),
            len(ar.events),
            len(ar.failed),
        )
        report[code] = entry

    lead_payload = leaders_payload(lead_results)
    appt_payload = appointments_payload(appt_results)
    dump_json("output/shanghai_special_manual_leaders.json", lead_payload)
    dump_json("output/shanghai_special_manual_appointments.json", appt_payload)
    dump_json("output/shanghai_special_manual_report.json", report)

    if args.dry_run:
        logging.info("dry-run: skip DB write")
        conn.close()
        return

    if lead_results:
        logging.info("ingest lead %s", ingest_leader_results(lead_payload, conn=conn))
    if appt_results:
        logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
    n = recompute_persons(conn)
    logging.info("recompute_persons %s", n)
    conn.commit()
    conn.close()
    logging.info("done report=%s", report)


if __name__ == "__main__":
    main()
