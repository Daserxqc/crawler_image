"""Ingest Hebei from user-saved HTML; optional headless-only notice fetch."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.crawler.leader_intro import parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.store import ingest_appointment_results, ingest_leader_results, list_persons

OUT = ROOT / "output"
APPT_LIST_URL = "http://hebei.chinatax.gov.cn/hbswxxgk/gkml/1166/1247/1757/index.html"
LEADER_URL = "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html"
APPT_LIMIT = 15


def _newest_html(*needles: str) -> Path:
    cands = [
        p
        for p in OUT.glob("*.html")
        if all(n in p.name for n in needles)
    ]
    if not cands:
        raise FileNotFoundError(f"No HTML in {OUT} matching {needles}")
    return max(cands, key=lambda p: p.stat().st_mtime)


def _hebei_appt_list_html() -> Path:
    """Locate appointment list fragment under Hebei xxgk _files (saved as 1167.html)."""
    for files_dir in sorted(OUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not files_dir.is_dir() or "河北" not in files_dir.name:
            continue
        candidate = files_dir / "1167.html"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Hebei appointment list HTML (1167.html) not found under output/*河北*_files")


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _saved_url(html: str, fallback: str) -> str:
    match = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html)
    return match.group(1) if match else fallback


def ingest_leaders() -> dict:
    path = _newest_html("河北", "领导简介")
    html = _read_html(path)
    url = _saved_url(html, LEADER_URL)
    leaders = parse_leader_intro(html, url, "hebei")
    result = LeaderCrawlResult(
        bureau="hebei",
        hub_url=url,
        page_count=1,
        leaders=leaders,
    )
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / "hebei_leaders.json", payload)
    ingested = ingest_leader_results(payload)
    return {
        "source": str(path),
        "url": url,
        "leaders": len(leaders),
        "ingested": ingested,
        "names": [d.person_name for d in leaders],
    }


def ingest_appointments(*, try_live: bool = True) -> dict:
    list_html_path = _hebei_appt_list_html()

    list_html = _read_html(list_html_path)
    list_url = _saved_url(list_html, APPT_LIST_URL)
    items = parse_appointment_list(list_html, list_url)[:APPT_LIMIT]

    notices = []
    events = []
    failed: list[dict[str, str]] = []
    session = create_session() if try_live else None

    for item in items:
        if not try_live or session is None:
            failed.append({"url": item.source_url, "error": "skipped live fetch (manual list only)"})
            continue
        try:
            time.sleep(0.2)
            # allow_browser=True uses headless-only Playwright/Drission (no visible windows)
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
                allow_browser=True,
            )
            notice = parse_appointment_detail(detail_html, detail_url, "hebei")
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)[:500]})
            logging.warning("Notice fetch failed %s: %s", item.source_url, exc)

    result = AppointmentCrawlResult(
        bureau="hebei",
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / "hebei_appointments.json", payload)
    new_events = ingest_appointment_results(payload)
    return {
        "source": str(list_html_path),
        "url": list_url,
        "list_items": len(items),
        "notices": len(notices),
        "events": len(events),
        "failed": len(failed),
        "new_events_ingested": new_events,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try_live = True
    summary: dict = {"code": "hebei", "mode": "manual+headless"}
    summary["leaders"] = ingest_leaders()

    list_path = _hebei_appt_list_html()
    list_html = _read_html(list_path)
    list_url = _saved_url(list_html, APPT_LIST_URL)
    items = parse_appointment_list(list_html, list_url)[:1]
    if items:
        try:
            session = create_session()
            fetch_html(session, items[0].source_url, referer=list_url, allow_browser=True)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Headless notice probe failed — list-only mode: %s", exc)
            try_live = False

    summary["appointments"] = ingest_appointments(try_live=try_live)
    hebei_persons = list(list_persons(bureau_code="hebei"))
    summary["hebei_persons_in_db"] = len(hebei_persons)
    summary["L"] = summary["leaders"]["leaders"]
    summary["E"] = summary["appointments"]["events"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
