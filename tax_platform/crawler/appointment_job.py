"""Crawl jobs for personnel appointment notices."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import is_appointment_list_url, parse_appointment_list
from tax_platform.crawler.crawl_state import load_crawl_state, mark_crawl_result, save_crawl_state
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.crawler.jpage import (
    fetch_dataproxy_html,
    fetch_dataproxy_pages,
    find_dataproxy_url,
    materialize_list_html,
)
from tax_platform.crawler.xxgk_list import fetch_xxgk_list_html
from tax_platform.models.entities import AppointmentEvent, NoticeMeta

KIND = "appointments"


def _looks_like_list_notice(notice: NoticeMeta) -> bool:
    title = notice.title or ""
    if re.search(r"共\s*\d+\s*条", title):
        return True
    body = notice.raw_text or ""
    # List pages dump many「关于…通知 2026-…」lines without appointment clauses.
    title_hits = len(re.findall(r"关于.{2,40}通知", body))
    appoint_hits = len(re.findall(r"(?:任命|免去|任).{0,20}(?:处长|科长|局长|主任)", body))
    return title_hits >= 5 and appoint_hits == 0


@dataclass
class AppointmentCrawlResult:
    bureau: str
    list_url: str
    list_count: int
    notices: list[NoticeMeta] = field(default_factory=list)
    events: list[AppointmentEvent] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    skipped: int = 0


def _load_appointment_list_html(session, list_url: str) -> tuple[str, str]:
    # Warm WAF cookies on the column shell (drop ?number=) when needed.
    if "chinatax.gov.cn" in list_url and "number=" in list_url:
        shell = list_url.split("?", 1)[0]
        try:
            fetch_html(session, shell, follow_meta_refresh=False)
        except Exception:  # noqa: BLE001
            pass
    final_url, html = fetch_html(session, list_url, follow_meta_refresh=False)
    items_html = materialize_list_html(html)
    wcm = _fetch_wcm_static_pages(session, final_url, html)
    if wcm:
        return final_url, wcm
    if parse_appointment_list(items_html, final_url):
        # Prefer paginated dataproxy when available.
        proxy = find_dataproxy_url(html, final_url)
        if proxy:
            paged = fetch_dataproxy_pages(session, proxy, referer=final_url)
            if paged and len(parse_appointment_list(paged, final_url)) > len(
                parse_appointment_list(items_html, final_url)
            ):
                return final_url, paged
        return final_url, items_html

    xxgk_html = fetch_xxgk_list_html(session, final_url, html)
    if xxgk_html and parse_appointment_list(xxgk_html, final_url):
        return final_url, xxgk_html

    proxy = find_dataproxy_url(html, final_url)
    if not proxy:
        return final_url, items_html
    proxy_html = fetch_dataproxy_pages(session, proxy, referer=final_url) or fetch_dataproxy_html(
        session, proxy, referer=final_url
    )
    return final_url, materialize_list_html(proxy_html)


def _fetch_wcm_static_pages(session, list_url: str, page_html: str) -> str | None:
    """Shanghai-style WCM static分页: index.html + index_1.html … index_{n-1}.html."""
    count_m = re.search(r"var\s+pagecount\s*=\s*(\d+)\s*;", page_html, re.I)
    name_m = re.search(r'var\s+pagename\s*=\s*"([^"]+)"\s*;', page_html, re.I)
    ext_m = re.search(r'var\s+pageext\s*=\s*"([^"]+)"\s*;', page_html, re.I)
    if not count_m:
        return None
    pagecount = int(count_m.group(1))
    if pagecount <= 1:
        return None
    pagename = name_m.group(1) if name_m else "index"
    pageext = ext_m.group(1) if ext_m else "html"
    from urllib.parse import urljoin

    base = list_url if list_url.endswith("/") else list_url.rsplit("/", 1)[0] + "/"
    chunks: list[str] = [materialize_list_html(page_html)]
    # pageindex 0 = current page (already fetched); pages 1..pagecount-1 → index_N.html
    for idx in range(1, pagecount):
        page_url = urljoin(base, f"{pagename}_{idx}.{pageext}")
        try:
            _, html = fetch_html(session, page_url, referer=list_url, follow_meta_refresh=False)
        except Exception as exc:  # noqa: BLE001
            logging.warning("WCM page failed %s: %s", page_url, exc)
            break
        material = materialize_list_html(html)
        if not parse_appointment_list(material, list_url):
            break
        chunks.append(material)
    merged = "\n".join(chunks)
    if len(parse_appointment_list(merged, list_url)) <= len(
        parse_appointment_list(chunks[0], list_url)
    ):
        return None
    return merged



def crawl_appointments_site(
    code: str,
    *,
    limit: int = 0,
    delay: float = 0.4,
    known_urls: set[str] | None = None,
    incremental: bool = True,
) -> AppointmentCrawlResult:
    """limit=0 means no cap (crawl every appointment link found on the list page).

    When *incremental* is True and *known_urls* is provided, detail pages whose URL
    is already known are skipped (notice-level delta crawl).
    """
    site = get_site(code)
    if not (site.appointment_list_url or "").strip():
        return AppointmentCrawlResult(
            bureau=code,
            list_url="",
            list_count=0,
            notices=[],
            events=[],
            failed=[{"url": "", "error": "no appointment_list_url"}],
            skipped=0,
        )
    session = create_session()
    list_url, list_html = _load_appointment_list_html(session, site.appointment_list_url)
    items = parse_appointment_list(list_html, list_url)
    if limit > 0:
        items = items[:limit]
    notices: list[NoticeMeta] = []
    events: list[AppointmentEvent] = []
    failed: list[dict[str, str]] = []
    skipped = 0
    known = known_urls or set()

    for item in items:
        if incremental and item.source_url in known:
            skipped += 1
            continue
        time.sleep(delay)
        try:
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
            )
            if is_appointment_list_url(detail_url, list_url=list_url):
                failed.append({"url": item.source_url, "error": "list_page_skipped"})
                logging.warning("Skip list page mistaken as notice: %s", detail_url)
                continue
            notice = parse_appointment_detail(detail_html, detail_url, site.code)
            if is_appointment_list_url(notice.source_url) or _looks_like_list_notice(notice):
                failed.append({"url": item.source_url, "error": "list_page_content_skipped"})
                logging.warning("Skip list-like notice content: %s", notice.source_url)
                continue
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
        skipped=skipped,
    )


def crawl_appointments(
    site: str = "pdtax",
    *,
    limit: int = 0,
    delay: float = 0.4,
    due_only: bool = False,
    level: str | None = None,
    record_state: bool = True,
    incremental: bool = True,
    known_urls: set[str] | None = None,
) -> AppointmentCrawlResult | list[AppointmentCrawlResult]:
    codes = resolve_site_codes(site, kind=KIND, due_only=due_only, level=level)
    if not codes:
        logging.info("No appointment sites due for crawl")
        return [] if site in {"all", "national"} or due_only else []

    results: list[AppointmentCrawlResult] = []
    state = load_crawl_state() if record_state else {}
    for code in codes:
        try:
            result = crawl_appointments_site(
                code,
                limit=limit,
                delay=delay,
                known_urls=known_urls,
                incremental=incremental,
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("Site %s failed: %s", code, exc)
            result = AppointmentCrawlResult(
                bureau=code,
                list_url=get_site(code).appointment_list_url,
                list_count=0,
                failed=[{"url": get_site(code).appointment_list_url, "error": str(exc)}],
            )
        results.append(result)
        logging.info(
            "appointments %s: %s notices, %s events, %s skipped, %s failed",
            code,
            len(result.notices),
            len(result.events),
            result.skipped,
            len(result.failed),
        )
        if record_state:
            mark_crawl_result(
                state,
                kind=KIND,
                site_code=code,
                ok=not result.failed or bool(result.notices) or result.skipped > 0,
            )
    if record_state:
        save_crawl_state(state)

    if site in {"all", "national"} or due_only or len(results) != 1:
        return results
    return results[0]


def appointments_payload(result: AppointmentCrawlResult | list[AppointmentCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
