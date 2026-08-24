"""Crawl jobs for leader introduction pages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tax_platform.config.sites import get_site
from tax_platform.crawler.crawl_state import load_crawl_state, mark_crawl_result, save_crawl_state
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.models.entities import LeaderDuty

KIND = "leaders"


@dataclass
class LeaderCrawlResult:
    bureau: str
    hub_url: str
    page_count: int
    leaders: list[LeaderDuty] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


def crawl_leaders_site(code: str, *, delay: float = 0.4) -> LeaderCrawlResult:
    site = get_site(code)
    session = create_session()
    hub_url, hub_html = fetch_html(session, site.leader_intro_url, follow_meta_refresh=True)
    pages = [hub_url]
    if not parse_leader_intro(hub_html, hub_url, site.code):
        pages = leader_page_targets(hub_html, hub_url) or [hub_url]

    leaders: list[LeaderDuty] = []
    failed: list[dict[str, str]] = []
    html_by_url = {hub_url: hub_html}

    for index, page_url in enumerate(pages):
        if delay and index:
            time.sleep(delay)
        try:
            if page_url not in html_by_url:
                final_url, html = fetch_html(
                    session,
                    page_url,
                    referer=hub_url,
                    follow_meta_refresh=True,
                )
            else:
                final_url, html = page_url, html_by_url[page_url]
            leaders.extend(parse_leader_intro(html, final_url, site.code))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": page_url, "error": str(exc)})
            logging.warning("Failed %s: %s", page_url, exc)

    return LeaderCrawlResult(
        bureau=site.code,
        hub_url=hub_url,
        page_count=len(pages),
        leaders=leaders,
        failed=failed,
    )


def crawl_leaders(
    site: str = "pdtax",
    *,
    delay: float = 0.4,
    due_only: bool = False,
    level: str | None = None,
    record_state: bool = True,
) -> LeaderCrawlResult | list[LeaderCrawlResult]:
    codes = resolve_site_codes(site, kind=KIND, due_only=due_only, level=level)
    if not codes:
        logging.info("No leader sites due for crawl")
        return [] if site == "all" or due_only else []

    results: list[LeaderCrawlResult] = []
    state = load_crawl_state() if record_state else {}
    for code in codes:
        result = crawl_leaders_site(code, delay=delay)
        results.append(result)
        if record_state:
            mark_crawl_result(
                state,
                kind=KIND,
                site_code=code,
                ok=not result.failed or bool(result.leaders),
            )
    if record_state:
        save_crawl_state(state)

    if site == "all" or due_only or len(results) != 1:
        return results
    return results[0]


def leaders_payload(result: LeaderCrawlResult | list[LeaderCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
