"""Crawl ALL pages of national STA 人事任免 via pager API, then ingest.

Pagination on the list page uses layui ``javascript:;`` links. Real data comes from:
POST https://www.chinatax.gov.cn/getFileListByCodeId
with channelId for n810611r (人事任免).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
from tax_platform.crawler.appointment_list import AppointmentListItem
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.store import ingest_appointment_results

OUT = ROOT / "output"
LIST_URL = "https://www.chinatax.gov.cn/chinatax/n810214/c102374/c102384/n810611r/"
API_URL = "https://www.chinatax.gov.cn/getFileListByCodeId"
# From page inline script: channelId for codeName=n810611r
CHANNEL_ID = "aea711f23a3b4592a23a538276bbdf94"
PAGE_SIZE = 20
MAX_PAGES = 80
EMPTY_STOP = 2
DELAY = 0.25
OUT_JSON = OUT / "national_appointments.json"
OUT_JSON_MANUAL = OUT / "national_appointments_from_manual.json"
SUMMARY_JSON = OUT / "sta_pagination_summary.json"


def _filter_notices(items: list[AppointmentListItem]) -> list[AppointmentListItem]:
    keep: list[AppointmentListItem] = []
    for item in items:
        title = item.title or ""
        if "年报" in title or "政府信息公开" in title:
            continue
        if "任免工作人员" in title or ("任免" in title and "工作人员" in title):
            keep.append(item)
        elif "任免" in title and "年报" not in title:
            keep.append(item)
    return keep


def _parse_published(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("http://www.chinatax.gov.cn"):
        url = "https://www.chinatax.gov.cn" + url[len("http://www.chinatax.gov.cn") :]
    return url


def _items_from_api_page(payload: dict) -> tuple[list[AppointmentListItem], int]:
    data = ((payload or {}).get("results") or {}).get("data") or {}
    total = int(data.get("total") or 0)
    results = data.get("results") or []
    items: list[AppointmentListItem] = []
    for row in results:
        title = (row.get("title") or row.get("titleHtml") or row.get("subTitleHtml") or "").strip()
        title = re.sub(r"<[^>]+>", "", title)
        title = re.sub(r"\s+", " ", title).strip()
        url = _normalize_url(str(row.get("url") or row.get("redirectUrl") or ""))
        if not title or not url:
            continue
        items.append(
            AppointmentListItem(
                title=title,
                source_url=url,
                published_on=_parse_published(row.get("publishedTimeStr")),
            )
        )
    return items, total


def _fetch_api_page(session, page: int) -> dict:
    data = {
        "codeId": "",
        "channelId": CHANNEL_ID,
        "keySYH": "syh",
        "keyFWZH": "fwzh",
        "keyTc": "ticai",
        "relateSubChannels": "true",
        "page": str(page),
        "size": str(PAGE_SIZE),
    }
    resp = session.post(
        API_URL,
        data=data,
        headers={
            "Referer": LIST_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.chinatax.gov.cn",
        },
        timeout=60,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def _collect_all_items(session) -> tuple[str, list[AppointmentListItem], int]:
    """Collect unique notice list items across all API pages."""
    # Warm session / WAF via list HTML (also useful as referer context).
    final, html = fetch_html(session, LIST_URL, allow_browser=True)
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.joinpath("_sta_live_list_p0.html").write_text(html, encoding="utf-8")

    all_items: dict[str, AppointmentListItem] = {}
    empty_streak = 0
    pages_crawled = 0
    total_hint = 0

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(DELAY)
        try:
            payload = _fetch_api_page(session, page)
        except Exception as exc:  # noqa: BLE001
            logging.warning("API page %s failed: %s", page, exc)
            empty_streak += 1
            if empty_streak >= EMPTY_STOP:
                break
            continue

        raw_items, total_hint = _items_from_api_page(payload)
        items = _filter_notices(raw_items)
        pages_crawled += 1
        new = 0
        for item in items:
            if item.source_url not in all_items:
                all_items[item.source_url] = item
                new += 1
        logging.info(
            "page=%s raw=%s filtered=%s new=%s unique=%s total_hint=%s",
            page,
            len(raw_items),
            len(items),
            new,
            len(all_items),
            total_hint,
        )
        OUT.joinpath(f"_sta_api_page_{page}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2)[:200_000],
            encoding="utf-8",
        )

        if new == 0 or len(raw_items) == 0:
            empty_streak += 1
            if empty_streak >= EMPTY_STOP:
                logging.info("stop: %s consecutive empty/no-new pages", empty_streak)
                break
        else:
            empty_streak = 0

        # Early stop when we have collected everything the API reports.
        if total_hint and len(all_items) >= total_hint and len(raw_items) < PAGE_SIZE:
            logging.info("stop: collected all %s reported items", total_hint)
            break

    # Fallback: static index_N.html if API yielded almost nothing.
    if len(all_items) <= 5:
        logging.warning("API yielded few items; trying index_N.html fallback")
        base = final if final.endswith("/") else final.rsplit("/", 1)[0] + "/"
        empty_streak = 0
        for i in range(1, MAX_PAGES):
            page_url = urljoin(base, f"index_{i}.html")
            time.sleep(DELAY)
            try:
                page_final, page_html = fetch_html(
                    session, page_url, referer=final, allow_browser=True
                )
            except Exception as exc:  # noqa: BLE001
                logging.info("index fallback stop at %s: %s", page_url, exc)
                break
            from tax_platform.crawler.appointment_list import parse_appointment_list

            items = _filter_notices(parse_appointment_list(page_html, page_final))
            pages_crawled += 1
            new = 0
            for item in items:
                if item.source_url not in all_items:
                    all_items[item.source_url] = item
                    new += 1
            logging.info("index_%s items=%s new=%s", i, len(items), new)
            if new == 0:
                empty_streak += 1
                if empty_streak >= EMPTY_STOP or len(items) == 0:
                    break
            else:
                empty_streak = 0

    return final, list(all_items.values()), pages_crawled


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session = create_session()
    list_url, items, pages_crawled = _collect_all_items(session)
    logging.info("TOTAL unique notices=%s pages_crawled=%s", len(items), pages_crawled)

    notices = []
    events = []
    failed: list[dict[str, str]] = []
    for i, item in enumerate(items, 1):
        time.sleep(DELAY)
        logging.info("[%s/%s] %s", i, len(items), item.title)
        try:
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
                allow_browser=True,
            )
            notice = parse_appointment_detail(detail_html, detail_url, "sta")
            notices.append(notice)
            extracted = extract_appointment_events(notice)
            events.extend(extracted)
            logging.info("  -> events=%s", len(extracted))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)})
            logging.warning("  FAIL %s", exc)

    result = AppointmentCrawlResult(
        bureau="sta",
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )
    payload = serialize_crawl_result(result)
    dump_json(OUT_JSON, payload)
    dump_json(OUT_JSON_MANUAL, payload)
    ingested = ingest_appointment_results(payload)
    summary = {
        "list_url": list_url,
        "pages_crawled": pages_crawled,
        "list_count": len(items),
        "notices": len(notices),
        "events": len(events),
        "failed": len(failed),
        "new_events_ingested": ingested,
        "sample_titles": [n.title for n in notices[:5]],
        "failed_urls": [f["url"] for f in failed[:20]],
    }
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
