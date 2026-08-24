"""Crawl jobs for personnel appointment notices."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tax_platform.config.sites_shanghai import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.models.entities import AppointmentEvent, NoticeMeta


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
) -> AppointmentCrawlResult | list[AppointmentCrawlResult]:
    codes = resolve_site_codes(site)
    results = [crawl_appointments_site(code, limit=limit, delay=delay) for code in codes]
    if site == "all":
        return results
    return results[0]


def appointments_payload(result: AppointmentCrawlResult | list[AppointmentCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
