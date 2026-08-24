"""Crawl jobs for personnel appointment notices."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.crawl_state import load_crawl_state, mark_crawl_result, save_crawl_state
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.models.entities import AppointmentEvent, NoticeMeta

KIND = "appointments"


@dataclass
class AppointmentCrawlResult:
    bureau: str
    list_url: str
    list_count: int
    notices: list[NoticeMeta] = field(default_factory=list)
    events: list[AppointmentEvent] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


def crawl_appointments_site(code: str, *, limit: int = 3, delay: float = 0.4) -> AppointmentCrawlResult:
    site = get_site(code)
    session = create_session()
    list_url, list_html = fetch_html(session, site.appointment_list_url, follow_meta_refresh=False)
    items = parse_appointment_list(list_html, list_url)[:limit]
    notices: list[NoticeMeta] = []
    events: list[AppointmentEvent] = []
    failed: list[dict[str, str]] = []

    for item in items:
        time.sleep(delay)
        try:
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
            )
            notice = parse_appointment_detail(detail_html, detail_url, site.code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)})
            logging.warning("Failed %s: %s", item.source_url, exc)

    return AppointmentCrawlResult(
        bureau=site.code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )


def crawl_appointments(
    site: str = "pdtax",
    *,
    limit: int = 3,
    delay: float = 0.4,
    due_only: bool = False,
    level: str | None = None,
    record_state: bool = True,
) -> AppointmentCrawlResult | list[AppointmentCrawlResult]:
    codes = resolve_site_codes(site, kind=KIND, due_only=due_only, level=level)
    if not codes:
        logging.info("No appointment sites due for crawl")
        return [] if site == "all" or due_only else []

    results: list[AppointmentCrawlResult] = []
    state = load_crawl_state() if record_state else {}
    for code in codes:
        result = crawl_appointments_site(code, limit=limit, delay=delay)
        results.append(result)
        if record_state:
            mark_crawl_result(
                state,
                kind=KIND,
                site_code=code,
                ok=not result.failed or bool(result.notices),
            )
    if record_state:
        save_crawl_state(state)

    if site == "all" or due_only or len(results) != 1:
        return results
    return results[0]


def appointments_payload(result: AppointmentCrawlResult | list[AppointmentCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
