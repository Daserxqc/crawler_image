"""List-only watcher: keep latest 1–2 heads; buffer all newer notices for weekly batch ingest.

Flow:
  1) scan all list pages; write discoveries into ``appointment_list_updates``
  2) after the full scan finishes, detail-crawl waiting rows → notices + events
  3) mark ``ingested_at`` (keep rows); heads table still only stores top 1–2
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tax_platform.config.list_heads_buckets import is_no_appt_site
from tax_platform.config.list_url_normalize import normalize_list_url
from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import (
    _load_appointment_list_html,
    _looks_like_list_notice,
)
from tax_platform.crawler.appointment_list import (
    AppointmentListItem,
    _url_key,
    is_appointment_list_url,
    parse_appointment_list,
)
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import resolve_site_codes, serialize_crawl_result
from tax_platform.models.entities import AppointmentEvent, NoticeMeta
from tax_platform.store.ingest import ingest_appointment_results, known_notice_urls
from tax_platform.store.list_heads import (
    ListHead,
    clear_list_scan_failure,
    clear_update_tags,
    heads_from_notices,
    list_open_scan_failures,
    list_waiting_bureaus,
    list_waiting_updates,
    load_heads,
    mark_updates_ingested,
    newest_published_on,
    record_list_scan_failure,
    replace_bureau_updates,
    replace_heads,
)


@dataclass
class ListHeadScanResult:
    bureau: str
    list_url: str
    list_count: int
    heads: list[ListHead] = field(default_factory=list)
    new_items: list[ListHead] = field(default_factory=list)
    baseline_newest: str | None = None
    crawled_newest: str | None = None
    updated: bool = False
    seeded: bool = False
    skipped_no_list: bool = False
    error: str | None = None


def _item_day(item: AppointmentListItem) -> str | None:
    if item.published_on is None:
        return None
    if isinstance(item.published_on, date):
        return item.published_on.isoformat()
    return str(item.published_on)[:10] or None


def _same_url(a: str, b: str) -> bool:
    return _url_key(a) == _url_key(b)


def order_list_items(items: list[AppointmentListItem]) -> list[AppointmentListItem]:
    """Dated newest-first, then undated in DOM order."""
    dated = [it for it in items if it.published_on is not None]
    undated = [it for it in items if it.published_on is None]
    dated.sort(key=lambda it: it.published_on or date.min, reverse=True)
    return dated + undated


def pick_top_heads(items: list[AppointmentListItem], *, limit: int = 2) -> list[ListHead]:
    ordered = order_list_items(items)
    heads: list[ListHead] = []
    for i, item in enumerate(ordered[:limit], start=1):
        heads.append(
            ListHead(
                rank=i,
                title=item.title,
                source_url=item.source_url,
                published_on=_item_day(item),
                has_update=0,
            )
        )
    return heads


def collect_new_since_baseline(
    items: list[AppointmentListItem],
    *,
    baseline_date: str | None,
    baseline_url: str | None,
) -> list[AppointmentListItem]:
    """Walk list (newest first) until 现有最新; return everything above it."""
    ordered = order_list_items(items)
    if not ordered:
        return []

    top = ordered[0]
    if baseline_url and _same_url(top.source_url, baseline_url):
        return []
    top_day = _item_day(top)
    if top_day and baseline_date and top_day[:10] < baseline_date[:10]:
        return []

    new_items: list[AppointmentListItem] = []
    for item in ordered:
        if baseline_url and _same_url(item.source_url, baseline_url):
            break
        day = _item_day(item)
        if day and baseline_date and day[:10] < baseline_date[:10]:
            break
        new_items.append(item)
    return new_items


def _to_heads(items: list[AppointmentListItem]) -> list[ListHead]:
    return [
        ListHead(
            rank=i,
            title=it.title,
            source_url=it.source_url,
            published_on=_item_day(it),
            has_update=0,
        )
        for i, it in enumerate(items, start=1)
    ]


def scan_bureau_list_heads(
    heads_conn,
    code: str,
    *,
    main_conn=None,
    session=None,
    delay: float = 0.0,
) -> ListHeadScanResult:
    """Compare list page vs heads DB; *main_conn* only used to seed from notices."""
    site = get_site(code)
    if is_no_appt_site(code):
        return ListHeadScanResult(
            bureau=code,
            list_url="",
            list_count=0,
            skipped_no_list=True,
        )
    list_url = normalize_list_url((site.appointment_list_url or "").strip())
    if not list_url:
        return ListHeadScanResult(
            bureau=code,
            list_url="",
            list_count=0,
            skipped_no_list=True,
        )

    owns = session is None
    if owns:
        session = create_session()
    try:
        if delay:
            time.sleep(delay)
        resolved_url, list_html = _load_appointment_list_html(
            session, list_url, raise_on_fetch_fail=True
        )
        items = parse_appointment_list(list_html, resolved_url)
        ordered_top = pick_top_heads(items, limit=1)
        crawled_newest = newest_published_on(ordered_top)

        stored = load_heads(heads_conn, code)
        from_table = bool(stored)
        if not stored and main_conn is not None:
            stored = heads_from_notices(main_conn, code, limit=1)
        baseline = newest_published_on(stored)
        baseline_url = stored[0].source_url if stored else None

        if not ordered_top:
            # Bootstrap from already-ingested notices when list page is temporarily
            # unreadable (WAF/JS), so later scans can compare against a real baseline.
            if not stored and main_conn is not None:
                seeded = heads_from_notices(main_conn, code, limit=2)
                if seeded:
                    replace_heads(heads_conn, code, seeded, has_update=False)
                    replace_bureau_updates(heads_conn, code, [])
                    clear_list_scan_failure(heads_conn, code)
                    return ListHeadScanResult(
                        bureau=code,
                        list_url=resolved_url,
                        list_count=0,
                        heads=seeded,
                        baseline_newest=newest_published_on(seeded),
                        crawled_newest=None,
                        seeded=True,
                    )
            clear_list_scan_failure(heads_conn, code)
            return ListHeadScanResult(
                bureau=code,
                list_url=resolved_url,
                list_count=0,
                heads=[],
                baseline_newest=baseline,
                crawled_newest=None,
            )

        if not stored:
            heads = pick_top_heads(items, limit=2)
            replace_heads(heads_conn, code, heads, has_update=False)
            replace_bureau_updates(heads_conn, code, [])
            clear_list_scan_failure(heads_conn, code)
            return ListHeadScanResult(
                bureau=code,
                list_url=resolved_url,
                list_count=len(items),
                heads=heads,
                baseline_newest=None,
                crawled_newest=crawled_newest,
                seeded=True,
            )

        new_raw = collect_new_since_baseline(
            items,
            baseline_date=baseline,
            baseline_url=baseline_url,
        )
        if not new_raw:
            if not from_table:
                replace_heads(heads_conn, code, stored[:1], has_update=False)
                clear_list_scan_failure(heads_conn, code)
                return ListHeadScanResult(
                    bureau=code,
                    list_url=resolved_url,
                    list_count=len(items),
                    heads=stored[:1],
                    baseline_newest=baseline,
                    crawled_newest=crawled_newest,
                    seeded=True,
                )
            clear_list_scan_failure(heads_conn, code)
            return ListHeadScanResult(
                bureau=code,
                list_url=resolved_url,
                list_count=len(items),
                heads=stored,
                baseline_newest=baseline,
                crawled_newest=crawled_newest,
                updated=False,
            )

        heads = pick_top_heads(items, limit=2)
        new_heads = _to_heads(new_raw)
        replace_heads(heads_conn, code, heads, has_update=True)
        replace_bureau_updates(
            heads_conn,
            code,
            [
                {
                    "title": h.title,
                    "source_url": h.source_url,
                    "published_on": h.published_on,
                }
                for h in new_heads
            ],
        )
        clear_list_scan_failure(heads_conn, code)
        return ListHeadScanResult(
            bureau=code,
            list_url=resolved_url,
            list_count=len(items),
            heads=heads,
            new_items=new_heads,
            baseline_newest=baseline,
            crawled_newest=crawled_newest,
            updated=True,
        )
    except Exception as exc:  # noqa: BLE001
        record_list_scan_failure(
            heads_conn, code, list_url=list_url, error=str(exc)
        )
        return ListHeadScanResult(
            bureau=code,
            list_url=list_url,
            list_count=0,
            error=str(exc),
        )
    finally:
        if owns:
            session.close()


def scan_all_list_heads(
    heads_conn,
    *,
    main_conn=None,
    site: str = "all",
    level: str | None = None,
    delay: float = 0.4,
    max_sites: int = 0,
    retry_failed: bool = True,
    retry_delay: float | None = None,
) -> list[ListHeadScanResult]:
    codes = resolve_site_codes(site, level=level)
    if max_sites > 0:
        codes = codes[:max_sites]
    results: list[ListHeadScanResult] = []
    session = create_session()
    try:
        for i, code in enumerate(codes, start=1):
            result = scan_bureau_list_heads(
                heads_conn,
                code,
                main_conn=main_conn,
                session=session,
                delay=delay,
            )
            results.append(result)
            _log_scan_result(result)
            heads_conn.commit()
            if i % 20 == 0:
                logging.info("list-heads progress %s/%s", i, len(codes))
        if retry_failed:
            results = retry_failed_list_heads(
                heads_conn,
                results,
                main_conn=main_conn,
                session=session,
                delay=retry_delay if retry_delay is not None else max(delay, 0.8),
            )
    finally:
        session.close()
    heads_conn.commit()
    return results


def _log_scan_result(result: ListHeadScanResult) -> None:
    code = result.bureau
    if result.error:
        logging.warning("list-heads %s FAILED: %s", code, result.error)
    elif result.skipped_no_list:
        logging.info("list-heads %s skip (no list url)", code)
    elif result.updated:
        logging.info(
            "list-heads %s UPDATE %s -> %s (%s new)",
            code,
            result.baseline_newest,
            result.crawled_newest,
            len(result.new_items),
        )
    elif result.seeded:
        logging.info("list-heads %s seeded newest=%s", code, result.crawled_newest)
    else:
        logging.info(
            "list-heads %s ok newest=%s (no newer)",
            code,
            result.crawled_newest,
        )


def merge_scan_results(
    results: list[ListHeadScanResult],
    retries: list[ListHeadScanResult],
) -> list[ListHeadScanResult]:
    """Replace first-pass rows with retry outcomes for the same bureau."""
    by_bureau = {r.bureau: r for r in results}
    for r in retries:
        by_bureau[r.bureau] = r
    # Preserve original order; append any unexpected new codes at end.
    ordered = []
    seen = set()
    for r in results:
        ordered.append(by_bureau[r.bureau])
        seen.add(r.bureau)
    for r in retries:
        if r.bureau not in seen:
            ordered.append(r)
    return ordered


def retry_failed_list_heads(
    heads_conn,
    results: list[ListHeadScanResult],
    *,
    main_conn=None,
    session=None,
    delay: float = 0.8,
    codes: list[str] | None = None,
) -> list[ListHeadScanResult]:
    """Re-scan first-pass failures once; merge successful retries into *results*."""
    failed_codes = codes or [r.bureau for r in results if r.error]
    # Also pick up any open DB markers not already in this run (e.g. prior run).
    if codes is None:
        marked = [row["bureau_code"] for row in list_open_scan_failures(heads_conn)]
        for code in marked:
            if code not in failed_codes:
                failed_codes.append(code)
    if not failed_codes:
        logging.info("list-heads retry: nothing to retry")
        return results

    logging.info("list-heads retry: re-scanning %s failed sites", len(failed_codes))
    owns = session is None
    if owns:
        session = create_session()
    retries: list[ListHeadScanResult] = []
    try:
        for i, code in enumerate(failed_codes, start=1):
            result = scan_bureau_list_heads(
                heads_conn,
                code,
                main_conn=main_conn,
                session=session,
                delay=delay,
            )
            retries.append(result)
            if result.error:
                logging.warning(
                    "list-heads retry %s/%s %s still FAILED: %s",
                    i,
                    len(failed_codes),
                    code,
                    result.error,
                )
            else:
                logging.info(
                    "list-heads retry %s/%s %s recovered newest=%s",
                    i,
                    len(failed_codes),
                    code,
                    result.crawled_newest,
                )
            heads_conn.commit()
    finally:
        if owns:
            session.close()

    recovered = sum(1 for r in retries if not r.error)
    still = sum(1 for r in retries if r.error)
    logging.info(
        "list-heads retry done: %s recovered, %s still failing",
        recovered,
        still,
    )
    return merge_scan_results(results, retries)


def _stub_notices_from_updates(rows: list[dict]) -> list[dict]:
    by_bureau: dict[str, list[dict]] = {}
    for row in rows:
        bureau = row["bureau_code"]
        day = (row.get("published_on") or "").strip()[:10] or None
        notice = {
            "bureau_code": bureau,
            "title": row.get("title") or "",
            "source_url": row.get("source_url") or "",
            "published_at": f"{day}T00:00:00" if day else None,
            "issued_on": None,
            "raw_text": "",
        }
        if not notice["source_url"]:
            continue
        by_bureau.setdefault(bureau, []).append(notice)
    return [
        {"bureau": bureau, "notices": notices, "events": []}
        for bureau, notices in by_bureau.items()
    ]


def _fetch_detail_for_url(
    *,
    bureau_code: str,
    source_url: str,
    list_url: str,
    delay: float,
) -> tuple[NoticeMeta | None, list[AppointmentEvent], dict[str, str] | None]:
    if delay:
        time.sleep(delay)
    session = create_session()
    try:
        detail_url, detail_html = fetch_html(
            session,
            source_url,
            referer=list_url or None,
            follow_meta_refresh=False,
        )
        if is_appointment_list_url(detail_url, list_url=list_url or None):
            return None, [], {"url": source_url, "error": "list_page_skipped"}
        notice = parse_appointment_detail(detail_html, detail_url, bureau_code)
        if is_appointment_list_url(notice.source_url) or _looks_like_list_notice(notice):
            return None, [], {"url": source_url, "error": "list_page_content_skipped"}
        return notice, extract_appointment_events(notice), None
    except Exception as exc:  # noqa: BLE001
        return None, [], {"url": source_url, "error": str(exc)}
    finally:
        session.close()


@dataclass
class _UrlCrawlResult:
    bureau: str
    list_url: str
    list_count: int
    notices: list[NoticeMeta] = field(default_factory=list)
    events: list[AppointmentEvent] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    skipped: int = 0


def aggregate_tagged_into_notices(
    heads_conn,
    main_conn,
    *,
    delay: float = 0.4,
    dry_run: bool = False,
) -> dict[str, Any]:
    """After all bureaus scanned: batch detail-crawl waiting updates → main DB notices/events."""
    waiting = list_waiting_updates(heads_conn)
    bureaus = list_waiting_bureaus(heads_conn)
    if not waiting:
        return {
            "updated_bureaus": 0,
            "pending_notices": 0,
            "ingested_events": 0,
            "stub_notices": 0,
            "crawl_results": [],
        }

    stub_payload = _stub_notices_from_updates(waiting)
    known = known_notice_urls(conn=main_conn)
    by_bureau: dict[str, list[dict]] = {}
    for row in waiting:
        by_bureau.setdefault(row["bureau_code"], []).append(row)

    crawl_payload: list[dict] = []
    for code, rows in by_bureau.items():
        site = get_site(code)
        list_url = site.appointment_list_url or ""
        result = _UrlCrawlResult(bureau=code, list_url=list_url, list_count=len(rows))
        for row in rows:
            url = row["source_url"]
            if url in known:
                result.skipped += 1
                continue
            notice, events, err = _fetch_detail_for_url(
                bureau_code=code,
                source_url=url,
                list_url=list_url,
                delay=delay,
            )
            if err:
                result.failed.append(err)
                logging.warning("aggregate detail fail %s: %s", err["url"], err["error"])
                continue
            if notice:
                result.notices.append(notice)
                result.events.extend(events)
                known.add(notice.source_url)
        crawl_payload.append(serialize_crawl_result(result))
        logging.info(
            "aggregate %s: %s notices, %s events, %s skipped, %s failed (of %s waiting)",
            code,
            len(result.notices),
            len(result.events),
            result.skipped,
            len(result.failed),
            len(rows),
        )

    ingested = 0
    stub_count = sum(len(p.get("notices") or []) for p in stub_payload)
    if not dry_run:
        ingest_appointment_results(stub_payload, conn=main_conn)
        ingested = ingest_appointment_results(crawl_payload, conn=main_conn)
        clear_update_tags(heads_conn, bureaus)
        mark_updates_ingested(heads_conn, bureaus)
        heads_conn.commit()
        main_conn.commit()

    return {
        "updated_bureaus": len(bureaus),
        "pending_notices": len(waiting),
        "tagged_heads": len(waiting),
        "ingested_events": ingested,
        "stub_notices": stub_count,
        "tagged": waiting,
        "crawl_results": crawl_payload,
    }
