"""Crawl jobs for leader introduction pages."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from tax_platform.config.sites import get_site
from tax_platform.crawler.crawl_state import load_crawl_state, mark_crawl_result, save_crawl_state
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.crawler.jpage import fetch_dataproxy_html, find_dataproxy_url, materialize_list_html
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


def _load_leader_hub(session, hub_url: str) -> tuple[str, str]:
    final_url, html = fetch_html(session, hub_url, follow_meta_refresh=True)
    if parse_leader_intro(html, final_url, "probe") or leader_page_targets(html, final_url):
        return final_url, html
    proxy = find_dataproxy_url(html, final_url)
    if not proxy:
        return final_url, html
    proxy_html = fetch_dataproxy_html(session, proxy, referer=final_url)
    return final_url, materialize_list_html(proxy_html)


def crawl_leaders_site(code: str, *, delay: float = 0.4) -> LeaderCrawlResult:
    site = get_site(code)
    session = create_session()
    hub_url, hub_html = _load_leader_hub(session, site.leader_intro_url)
    pages = [hub_url]
    for target in leader_page_targets(hub_html, hub_url):
        if target not in pages:
            pages.append(target)

    leaders_by_name: dict[str, LeaderDuty] = {}
    failed: list[dict[str, str]] = []
    html_by_url = {hub_url: hub_html}

    def _remember(duty: LeaderDuty) -> None:
        prev = leaders_by_name.get(duty.person_name)
        if prev is None:
            leaders_by_name[duty.person_name] = duty
            return
        # Prefer detail bios that carry oversight departments over hub stubs.
        prev_score = (len(prev.departments_raw or []), len(prev.duty_summary or ""), len(prev.title_raw or ""))
        new_score = (len(duty.departments_raw or []), len(duty.duty_summary or ""), len(duty.title_raw or ""))
        if new_score > prev_score:
            leaders_by_name[duty.person_name] = duty

    # If hub itself has profiles after materialize
    for duty in parse_leader_intro(hub_html, hub_url, site.code):
        _remember(duty)

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
            for duty in parse_leader_intro(html, final_url, site.code):
                _remember(duty)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": page_url, "error": str(exc)})
            logging.warning("Failed %s: %s", page_url, exc)

    return LeaderCrawlResult(
        bureau=site.code,
        hub_url=hub_url,
        page_count=len(pages),
        leaders=list(leaders_by_name.values()),
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
        return [] if site in {"all", "national"} or due_only else []

    results: list[LeaderCrawlResult] = []
    state = load_crawl_state() if record_state else {}
    for code in codes:
        try:
            result = crawl_leaders_site(code, delay=delay)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Site %s failed: %s", code, exc)
            result = LeaderCrawlResult(
                bureau=code,
                hub_url=get_site(code).leader_intro_url,
                page_count=0,
                failed=[{"url": get_site(code).leader_intro_url, "error": str(exc)}],
            )
        results.append(result)
        logging.info(
            "leaders %s: %s people, %s failed",
            code,
            len(result.leaders),
            len(result.failed),
        )
        if record_state:
            mark_crawl_result(
                state,
                kind=KIND,
                site_code=code,
                ok=not result.failed or bool(result.leaders),
            )
    if record_state:
        save_crawl_state(state)

    if site == "all" or due_only or site == "national" or len(results) != 1:
        return results
    return results[0]


def leaders_payload(result: LeaderCrawlResult | list[LeaderCrawlResult]) -> dict | list[dict]:
    if isinstance(result, list):
        return [serialize_crawl_result(item) for item in result]
    return serialize_crawl_result(result)
