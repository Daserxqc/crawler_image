"""Crawl jobs for leader introduction pages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tax_platform.config.sites_shanghai import get_site
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.models.entities import LeaderDuty


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
) -> LeaderCrawlResult | list[LeaderCrawlResult]:
    codes = resolve_site_codes(site)
    results = [crawl_leaders_site(code, delay=delay) for code in codes]
    if site == "all":
        return results
    return results[0]


def leaders_payload(result: LeaderCrawlResult | list[LeaderCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
