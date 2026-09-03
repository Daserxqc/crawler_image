"""Crawl jobs for personnel appointment notices."""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, urljoin, urlparse

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import is_appointment_list_url, parse_appointment_list
from tax_platform.crawler.crawl_state import load_crawl_state, mark_crawl_result, save_crawl_state
from tax_platform.crawler.http_client import apply_browser_cookies, create_session, fetch_html, fetch_html_browser
from tax_platform.crawler.shanghai_xxgk import fetch_shanghai_rsrm_list_html
from tax_platform.crawler.fujian_was5 import fetch_fujian_was5_list_html
from tax_platform.crawler.sta_chinatax import fetch_sta_list_html
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


def _qxtax_home_from_list_url(list_url: str) -> str | None:
    parsed = urlparse(list_url)
    match = re.search(r"/qxtax/([a-z][a-z0-9]{1,12})/", (parsed.path or "").lower())
    if not match:
        return None
    slug = match.group(1)
    return f"{parsed.scheme or 'https'}://{parsed.netloc}/qxtax/{slug}/"


def _warm_qxtax_session(session, list_url: str) -> None:
    """Establish Ruishu cookies before ``/api/queryGwxxQx`` (needs browser session)."""
    home = _qxtax_home_from_list_url(list_url)
    if not home:
        return
    try:
        burl, _, cookies = fetch_html_browser(list_url, timeout=60)
        apply_browser_cookies(session, cookies)
        if burl and burl != list_url:
            apply_browser_cookies(session, cookies)
    except Exception as exc:  # noqa: BLE001
        logging.debug("qxtax browser warm failed for %s: %s", list_url, exc)
        for url in (home, list_url):
            try:
                fetch_html(session, url, follow_meta_refresh=False)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.3)


def _fetch_qxtax_zwgk_list_html(session, list_url: str) -> str | None:
    """Chongqing ``/qxtax/{slug}/zwgk/`` lists load via ``/api/queryGwxxQx``."""
    parsed = urlparse(list_url)
    path = parsed.path.lower()
    if "/qxtax/" not in path or "/zwgk/" not in path:
        return None
    fbfl = (parse_qs(parsed.query).get("fbfldm") or [None])[0]
    if not fbfl:
        return None

    api = f"{parsed.scheme or 'https'}://{parsed.netloc}/api/queryGwxxQx"
    rows: list[dict] = []
    page_size = 50

    def _post_page(index: int) -> dict | None:
        payload = {
            "title": "",
            "fbsj": "",
            "fbflDm": fbfl,
            "pageSize": str(page_size),
            "pageIndex": str(index),
        }
        resp = session.post(
            api,
            data={"jsonstr": json.dumps(payload, ensure_ascii=False)},
            timeout=30,
            headers={"Referer": list_url},
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            return None
        return body

    _warm_qxtax_session(session, list_url)
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)
        rows = []
        page_index = 0
        try:
            while True:
                body = _post_page(page_index)
                if body is None:
                    break
                batch = body.get("data") or []
                if not batch:
                    break
                rows.extend(batch)
                total = int(body.get("total") or 0)
                if total and len(rows) >= total:
                    break
                if len(batch) < page_size:
                    break
                page_index += 1
        except Exception as exc:  # noqa: BLE001
            logging.warning("qxtax list API failed %s (attempt %s): %s", list_url, attempt + 1, exc)
            rows = []
            continue
        if rows:
            break

    if not rows:
        # Fallback: parse list links from browser-rendered zwgk page.
        try:
            _, bhtml, cookies = fetch_html_browser(list_url, timeout=60)
            apply_browser_cookies(session, cookies)
            if bhtml and parse_appointment_list(bhtml, list_url):
                return bhtml
        except Exception as exc:  # noqa: BLE001
            logging.debug("qxtax browser list fallback failed: %s", exc)
        return None
    links = []
    for row in rows:
        href = row.get("docpuburl") or ""
        title = row.get("doctitle") or ""
        if href and title:
            links.append(f'<a href="{href}">{title}</a>')
    return f"<html><body>{''.join(links)}</body></html>"


def _fetch_list_iframe_html(session, list_url: str, page_html: str) -> str | None:
    """Expand list iframes (e.g. Qingdao ``./index_1004.html``) and parse against the shell URL."""
    if parse_appointment_list(materialize_list_html(page_html), list_url):
        return None
    srcs = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', page_html, re.I)
    for src in srcs:
        if not src or src.startswith(("javascript:", "about:")):
            continue
        if not re.search(r"index_\d+\.html?", src, re.I) and "list" not in src.lower():
            # Still try relative index_*.html under rsrm shells.
            if "index_" not in src.lower():
                continue
        iframe_url = urljoin(list_url if list_url.endswith("/") else list_url.rsplit("/", 1)[0] + "/", src)
        try:
            _, iframe_html = fetch_html(
                session, iframe_url, referer=list_url, follow_meta_refresh=False
            )
        except Exception as exc:  # noqa: BLE001
            logging.debug("list iframe fetch failed %s: %s", iframe_url, exc)
            continue
        material = materialize_list_html(iframe_html)
        # Resolve children against the shell list URL, not the iframe path.
        if parse_appointment_list(material, list_url):
            return material
    return None


def _fetch_yunnan_col_dataproxy(session, list_url: str, page_html: str) -> str | None:
    """Yunnan 人事任免 col pages: list is ``/col/colN/``; details are ``/art/.../art_M_…``.

    Some city cols omit the jpage ``unitid`` in the shell; reuse the shared unitid
    seen on working sibling cols (e.g. 德宏 col8329 → unitid=7837).
    """
    if "yunnan.chinatax.gov.cn" not in list_url:
        return None
    if find_dataproxy_url(page_html, list_url):
        return None
    col_m = re.search(r"/col/col(\d+)/", list_url)
    if not col_m:
        return None
    column_id = col_m.group(1)
    # Prefer unitids already present anywhere on the shell; else known working ones.
    unit_ids = list(dict.fromkeys(re.findall(r"unitid[=:]?\s*[\"']?(\d+)", page_html, re.I)))
    for fallback in ("7837", "22194", "22384"):
        if fallback not in unit_ids:
            unit_ids.append(fallback)
    base = "http://yunnan.chinatax.gov.cn/"
    for unit_id in unit_ids[:6]:
        proxy = (
            "https://yunnan.chinatax.gov.cn/module/web/jpage/dataproxy.jsp?"
            f"page=1&webid=1&path={quote(base, safe='')}&columnid={column_id}"
            f"&unitid={unit_id}&webname={quote('国家税务总局云南省税务局')}&permissiontype=0"
        )
        try:
            html = fetch_dataproxy_pages(session, proxy, referer=list_url) or fetch_dataproxy_html(
                session, proxy, referer=list_url
            )
        except Exception as exc:  # noqa: BLE001
            logging.debug("yunnan dataproxy %s unit %s: %s", column_id, unit_id, exc)
            continue
        material = materialize_list_html(html or "")
        if parse_appointment_list(material, list_url):
            return material
    return None


def _load_appointment_list_html(
    session,
    list_url: str,
    *,
    raise_on_fetch_fail: bool = False,
) -> tuple[str, str]:
    shanghai_xml = fetch_shanghai_rsrm_list_html(session, list_url)
    if shanghai_xml and parse_appointment_list(shanghai_xml, list_url):
        return list_url, shanghai_xml

    sta_html = fetch_sta_list_html(session, list_url)
    if sta_html and parse_appointment_list(sta_html, list_url):
        return list_url, sta_html

    qxtax_html = _fetch_qxtax_zwgk_list_html(session, list_url)
    if qxtax_html and parse_appointment_list(qxtax_html, list_url):
        return list_url, qxtax_html
    # Warm WAF cookies on the column shell (drop ?number=) when needed.
    if "chinatax.gov.cn" in list_url and "number=" in list_url:
        shell = list_url.split("?", 1)[0]
        try:
            fetch_html(session, shell, follow_meta_refresh=False)
        except Exception:  # noqa: BLE001
            pass
    try:
        final_url, html = fetch_html(session, list_url, follow_meta_refresh=False)
    except Exception as exc:  # noqa: BLE001
        logging.warning("list fetch failed %s: %s", list_url, exc)
        shanghai_xml = fetch_shanghai_rsrm_list_html(session, list_url)
        if shanghai_xml and parse_appointment_list(shanghai_xml, list_url):
            return list_url, shanghai_xml
        if raise_on_fetch_fail and not (qxtax_html or "").strip():
            raise RuntimeError(f"list fetch failed: {exc}") from exc
        return list_url, qxtax_html or ""
    items_html = materialize_list_html(html)
    wcm = _fetch_wcm_static_pages(session, final_url, html)
    if wcm:
        return final_url, wcm

    # Qingdao-style: list rows live in iframe (index_1004.html), shell has no art links.
    iframe_html = _fetch_list_iframe_html(session, final_url, html)
    if iframe_html and parse_appointment_list(iframe_html, final_url):
        return final_url, iframe_html

    # Yunnan col shell sometimes omits jpage unitid; synthesize dataproxy from column id.
    yunnan_html = _fetch_yunnan_col_dataproxy(session, final_url, html)
    if yunnan_html and parse_appointment_list(yunnan_html, final_url):
        return final_url, yunnan_html

    if parse_appointment_list(items_html, final_url):
        # Prefer paginated dataproxy when available; keep first page if session POST 412s.
        proxy = find_dataproxy_url(html, final_url)
        if proxy:
            try:
                paged = fetch_dataproxy_pages(session, proxy, referer=final_url)
                if paged and len(parse_appointment_list(paged, final_url)) > len(
                    parse_appointment_list(items_html, final_url)
                ):
                    return final_url, paged
            except Exception as exc:  # noqa: BLE001
                logging.debug("dataproxy pagination skipped for %s: %s", final_url, exc)
        return final_url, items_html

    # Fujian city 主动公开目录 shell: rows come from WAS5 search (chnlid), not static HTML.
    if (
        "fujian.chinatax.gov.cn" in final_url
        and "/zfxxgkml/" in final_url
        and not parse_appointment_list(items_html, final_url)
    ):
        try:
            fj_html = fetch_fujian_was5_list_html(session, final_url, html)
            if fj_html and parse_appointment_list(fj_html, final_url):
                return final_url, fj_html
        except Exception as exc:  # noqa: BLE001
            logging.debug("fujian was5 list skipped for %s: %s", final_url, exc)
        # Last resort: browser zTree click (slow / flaky — avoid when WAS5 works).
        try:
            burl, bhtml, cookies = fetch_html_browser(final_url, timeout=60)
            apply_browser_cookies(session, cookies)
            bitems = materialize_list_html(bhtml)
            if parse_appointment_list(bitems, burl):
                return burl, bitems
            m = re.search(
                r"https?://fujian\.chinatax\.gov\.cn/[^\"'\s]+/(?:rsxx_\d+|rsrm)/?",
                bhtml,
                re.I,
            )
            if m:
                cand = m.group(0)
                if not cand.rstrip("/").endswith((".htm", ".html")):
                    _, ch, cookies2 = fetch_html_browser(cand, timeout=60)
                    apply_browser_cookies(session, cookies2)
                    citems = materialize_list_html(ch)
                    if parse_appointment_list(citems, cand):
                        return cand, citems
        except Exception as exc:  # noqa: BLE001
            logging.debug("fujian ztree/list browser skipped for %s: %s", final_url, exc)

    # Shanxi son/list: HTTP 200 shell with JS-rendered rows — needs browser wait.
    if "/son/list/" in final_url and not parse_appointment_list(items_html, final_url):
        try:
            burl, bhtml, cookies = fetch_html_browser(final_url, timeout=60)
            apply_browser_cookies(session, cookies)
            bitems = materialize_list_html(bhtml)
            if parse_appointment_list(bitems, burl):
                return burl, bitems
        except Exception as exc:  # noqa: BLE001
            logging.debug("son/list browser fetch skipped for %s: %s", final_url, exc)

    xxgk_html = fetch_xxgk_list_html(session, final_url, html)
    if xxgk_html and parse_appointment_list(xxgk_html, final_url):
        return final_url, xxgk_html

    proxy = find_dataproxy_url(html, final_url)
    if not proxy:
        return final_url, items_html
    try:
        proxy_html = fetch_dataproxy_pages(session, proxy, referer=final_url) or fetch_dataproxy_html(
            session, proxy, referer=final_url
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug("dataproxy fetch skipped for %s: %s", final_url, exc)
        return final_url, items_html
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
    detail_workers: int = 1,
    session=None,
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
    owns_session = session is None
    if owns_session:
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
    pending = [item for item in items if not (incremental and item.source_url in known)]
    skipped = len(items) - len(pending)

    def _fetch_detail(item):
        if delay:
            time.sleep(delay)
        thread_session = create_session()
        try:
            detail_url, detail_html = fetch_html(
                thread_session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
            )
            if is_appointment_list_url(detail_url, list_url=list_url):
                return None, None, {"url": item.source_url, "error": "list_page_skipped"}
            notice = parse_appointment_detail(detail_html, detail_url, site.code)
            if is_appointment_list_url(notice.source_url) or _looks_like_list_notice(notice):
                return None, None, {"url": item.source_url, "error": "list_page_content_skipped"}
            evs = extract_appointment_events(notice)
            return notice, evs, None
        except Exception as exc:  # noqa: BLE001
            return None, None, {"url": item.source_url, "error": str(exc)}
        finally:
            thread_session.close()

    workers = max(1, min(detail_workers, len(pending) or 1))
    if workers == 1:
        for item in pending:
            notice, evs, err = _fetch_detail(item)
            if err:
                failed.append(err)
                logging.warning("Failed %s: %s", err["url"], err["error"])
                continue
            if notice:
                notices.append(notice)
                events.extend(evs)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_detail, item): item for item in pending}
            for fut in as_completed(futures):
                notice, evs, err = fut.result()
                if err:
                    failed.append(err)
                    logging.warning("Failed %s: %s", err["url"], err["error"])
                    continue
                if notice:
                    notices.append(notice)
                    events.extend(evs)

    if owns_session:
        session.close()

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
