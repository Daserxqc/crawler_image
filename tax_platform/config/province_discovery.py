"""Discover per-province leader / appointment list URLs."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tax_platform.config.sites_provinces import PROVINCE_SUBDOMAINS, SUBDOMAIN_ALIASES
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.jpage import fetch_dataproxy_html, find_dataproxy_url, materialize_list_html
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.xxgk_list import fetch_xxgk_list_html

HOST_ALIASES: dict[str, list[str]] = {
    "neimenggu": ["nm", "neimenggu"],
    "xizang": ["xizang", "xz"],
    "shaanxi": ["shaanxi", "sn"],
}

# Known non-standard paths (seed before homepage crawl). Absolute URLs allowed.
KNOWN_PATHS: dict[str, dict[str, str]] = {
    "hebei": {
        "home_url": "http://hebei.chinatax.gov.cn/",
        "leader_intro_url": "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html",
        "appointment_list_url": "http://hebei.chinatax.gov.cn/hbswxxgk/gkml/1166/1247/1757/index.html",
    },
    "jilin": {
        "home_url": "https://jilin.chinatax.gov.cn/",
        "leader_intro_url": "https://jilin.chinatax.gov.cn/col/col24472/index.html",
        "appointment_list_url": "https://jilin.chinatax.gov.cn/col/col8211/index.html",
    },
    "jiangxi": {
        "home_url": "https://jiangxi.chinatax.gov.cn/",
        "leader_intro_url": "https://jiangxi.chinatax.gov.cn/col/col39358/index.html",
        "appointment_list_url": "https://jiangxi.chinatax.gov.cn/col/col32613/index.html",
    },
    "yunnan": {
        "home_url": "https://yunnan.chinatax.gov.cn/",
        "leader_intro_url": "https://yunnan.chinatax.gov.cn/col/col4701/index.html",
        "appointment_list_url": "https://yunnan.chinatax.gov.cn/col/col8641/index.html",
    },
    "xizang": {
        "home_url": "https://xizang.chinatax.gov.cn/",
        "leader_intro_url": "https://xizang.chinatax.gov.cn/col/col15212/index.html",
        "appointment_list_url": "https://xizang.chinatax.gov.cn/col/col15000/index.html",
    },
    "shaanxi": {
        "home_url": "https://shaanxi.chinatax.gov.cn/",
        "leader_intro_url": "https://shaanxi.chinatax.gov.cn/col/col36377/index.html",
        "appointment_list_url": "https://shaanxi.chinatax.gov.cn/col/col9016/index.html",
    },
    "gansu": {
        "home_url": "http://gansu.chinatax.gov.cn/",
        "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col3858/index.html",
        "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col129/index.html?number=A00009B00001A00016",
    },
    "beijing": {
        "home_url": "http://beijing.chinatax.gov.cn/",
        "leader_intro_url": "http://beijing.chinatax.gov.cn/bjswj/ldxx01/ldjianjie.shtml",
        "appointment_list_url": "http://beijing.chinatax.gov.cn/bjswj/c104184/tz.shtml",
    },
    "fujian": {
        "home_url": "https://fujian.chinatax.gov.cn/",
        "leader_intro_url": "https://fujian.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://fujian.chinatax.gov.cn/xxgk/rsrm/",
    },
    "hunan": {
        "home_url": "https://hunan.chinatax.gov.cn/",
        "leader_intro_url": "https://hunan.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://hunan.chinatax.gov.cn/xxgk/rsrm/",
    },
    "chongqing": {
        "home_url": "https://chongqing.chinatax.gov.cn/cqtax/",
        "leader_intro_url": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/ldjj/",
        "appointment_list_url": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/rsxx/rsrm/",
    },
    "shandong": {
        "home_url": "https://shandong.chinatax.gov.cn/",
        "leader_intro_url": "https://shandong.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://shandong.chinatax.gov.cn/xxgk/rsrm/",
    },
    "guangdong": {
        "home_url": "https://guangdong.chinatax.gov.cn/gdsw/index.shtml",
        "leader_intro_url": "https://guangdong.chinatax.gov.cn/gdsw/ldzl/leader.shtml",
        "appointment_list_url": "https://guangdong.chinatax.gov.cn/gdzdgkjbml/mlrsrm/zdgk_zfxxgk_list.shtml",
    },
    "jiangsu": {
        "leader_intro_url": "https://jiangsu.chinatax.gov.cn/art/2026/7/3/art_7642_1718741.html",
        "appointment_list_url": "https://jiangsu.chinatax.gov.cn/col/col21776/index.html",
    },
    "zhejiang": {
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col10697/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col24799/index.html",
        "home_url": "https://zhejiang.chinatax.gov.cn/",
    },
    "anhui": {
        "leader_intro_url": "https://anhui.chinatax.gov.cn/col/col7889/index.html",
        "appointment_list_url": "https://anhui.chinatax.gov.cn/col/col22602/index.html",
    },
    "liaoning": {
        "leader_intro_url": "https://liaoning.chinatax.gov.cn/col/col6555/index.html",
        "appointment_list_url": "https://liaoning.chinatax.gov.cn/col/col1214/index.html",
    },
    "neimenggu": {
        "leader_intro_url": "https://neimenggu.chinatax.gov.cn/xxgk/ldjj/",
    },
    "hubei": {
        "appointment_list_url": "http://hubei.chinatax.gov.cn/hbsw/xxgk/rsrm/index.html",
        "leader_intro_url": "http://hubei.chinatax.gov.cn/hbsw/xxgk/ldjj/index.html",
    },
    "guangxi": {
        "leader_intro_url": "https://guangxi.chinatax.gov.cn/xxgk/ldjj/202508/t20250820_421700.html",
        "appointment_list_url": "https://guangxi.chinatax.gov.cn/xxgk/zfxxgk_22500/fdzdgknr/rsxx_22567/rsrm_22568/",
    },
    "guizhou": {
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/xxgk/rsrm1/",
    },
    "neimenggu": {
        "leader_intro_url": "https://neimenggu.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://neimenggu.chinatax.gov.cn/xxgk/rsxx_25345/",
    },
    "sichuan": {
        "leader_intro_url": "https://sichuan.chinatax.gov.cn/col/col20404/index.html",
        "appointment_list_url": "https://sichuan.chinatax.gov.cn/col/col20006/index.html?number=A002001",
    },
    "ningxia": {
        "leader_intro_url": "https://ningxia.chinatax.gov.cn/col/col14807/index.html",
        "appointment_list_url": "https://ningxia.chinatax.gov.cn/col/col10883/index.html",
    },
    "hainan": {
        "leader_intro_url": "https://hainan.chinatax.gov.cn/xxgk_1_13_1/",
        "appointment_list_url": "https://hainan.chinatax.gov.cn/xxgk_3/",
    },
    "xinjiang": {
        "leader_intro_url": "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/jggk/ldjj/202605/t20260521_157910.html",
        "appointment_list_url": "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/rsjy/rsrm_22397/",
    },
    "tianjin": {
        "leader_intro_url": "https://tianjin.chinatax.gov.cn/u_zlmView.action?fjdm=11200000000&lmdm=010002",
        "appointment_list_url": "https://tianjin.chinatax.gov.cn/u_zlmViewMx.action?fjdm=11200000000&lmdm=01000401",
    },
    "heilongjiang": {
        "leader_intro_url": "https://heilongjiang.chinatax.gov.cn/col/col11194/index.html",
        "appointment_list_url": "https://heilongjiang.chinatax.gov.cn/col/col11190/index.html",
    },
    "shanxi": {
        "home_url": "https://shanxi.chinatax.gov.cn/",
        "leader_intro_url": "https://shanxi.chinatax.gov.cn/xxgk/leader/sx-11400",
        "appointment_list_url": "https://shanxi.chinatax.gov.cn/web/list/sx-11400-4187",
    },
    "qinghai": {
        "home_url": "http://qinghai.chinatax.gov.cn/",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/web/sjtwz/ldjs.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/web/rmgg/xxgk_fdzd_list.shtml",
    },
}

LEADER_PATHS = [
    "/xxgk/ldjj/",
    "/xxgk/ldjj/index.html",
    "/xxgk/ldjs/",
    "/gdsw/ldzl/leader.shtml",
]

APPOINTMENT_PATHS = [
    "/xxgk/rsxx/",
    "/xxgk/rsrm/",
    "/xxgk/rsxx/index.html",
    "/gdzdgkjbml/mlrsrm/zdgk_zfxxgk_list.shtml",
]

LINK_LEADER_RE = re.compile(r"领导(?:简介|介绍|信息)|ldjj|ldzl|ldjs", re.I)
LINK_APPOINTMENT_RE = re.compile(r"人事(?:任免|信息)|rsxx|rsrm|mlrsrm", re.I)


@dataclass
class ProvinceUrlDiscovery:
    code: str
    home_url: str | None = None
    leader_intro_url: str | None = None
    appointment_list_url: str | None = None
    leader_count: int = 0
    appointment_count: int = 0
    notes: list[str] = field(default_factory=list)


def _hosts_for(code: str, subdomain: str) -> list[str]:
    hosts: list[str] = []
    for candidate in (SUBDOMAIN_ALIASES.get(subdomain), subdomain, *HOST_ALIASES.get(code, [])):
        if candidate and candidate not in hosts:
            hosts.append(candidate)
    return hosts


def _base_urls(host: str) -> list[str]:
    return [f"https://{host}.chinatax.gov.cn"]


def _links_from_html(html: str, page_url: str, pattern: re.Pattern[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not href or href.startswith("javascript:"):
            continue
        if not pattern.search(href) and not pattern.search(text):
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _leader_score(url: str, html: str, code: str) -> int:
    leaders = parse_leader_intro(html, url, code)
    if leaders:
        return len(leaders)
    return len(leader_page_targets(html, url))


def _probe_leader(session, url: str, code: str, *, delay: float) -> tuple[str, int]:
    final_url, html = fetch_html(session, url, follow_meta_refresh=True)
    score = _leader_score(final_url, html, code)
    if score:
        return final_url, score
    best_url, best_score = final_url, 0
    for target in leader_page_targets(html, final_url)[:8]:
        if delay:
            time.sleep(delay)
        try:
            t_url, t_html = fetch_html(session, target, referer=final_url, follow_meta_refresh=True)
            t_score = _leader_score(t_url, t_html, code)
            if t_score > best_score:
                best_url, best_score = t_url, t_score
        except Exception:  # noqa: BLE001
            continue
    return best_url, best_score


def _probe_appointment(session, url: str) -> tuple[str, int]:
    final_url, html = fetch_html(session, url, follow_meta_refresh=False)
    items_html = materialize_list_html(html)
    score = len(parse_appointment_list(items_html, final_url))
    if score:
        return final_url, score
    xxgk_html = fetch_xxgk_list_html(session, final_url, html)
    if xxgk_html:
        score = len(parse_appointment_list(xxgk_html, final_url))
        if score:
            return final_url, score
    proxy = find_dataproxy_url(html, final_url)
    if not proxy:
        return final_url, 0
    proxy_html = fetch_dataproxy_html(session, proxy, referer=final_url)
    return final_url, len(parse_appointment_list(materialize_list_html(proxy_html), final_url))


def discover_province(code: str, subdomain: str, *, delay: float = 0.1) -> ProvinceUrlDiscovery:
    result = ProvinceUrlDiscovery(code=code)
    session = create_session()
    leader_best = ("", 0)
    appt_best = ("", 0)
    home_url: str | None = None

    # Prefer known absolute URLs first (fast path)
    known = KNOWN_PATHS.get(code, {})
    for key, url in known.items():
        if not url.startswith("http"):
            continue
        if delay:
            time.sleep(delay)
        try:
            if key == "leader_intro_url":
                final_url, score = _probe_leader(session, url, code, delay=delay)
                if score > leader_best[1]:
                    leader_best = (final_url, score)
            elif key == "appointment_list_url":
                final_url, score = _probe_appointment(session, url)
                if score > appt_best[1]:
                    appt_best = (final_url, score)
            elif key == "home_url":
                home_url = url
        except Exception as exc:  # noqa: BLE001
            result.notes.append(f"known {key} {url}: {exc}")

    if leader_best[1] and appt_best[1]:
        result.home_url = home_url
        result.leader_intro_url = leader_best[0]
        result.leader_count = leader_best[1]
        result.appointment_list_url = appt_best[0]
        result.appointment_count = appt_best[1]
        return result

    for host in _hosts_for(code, subdomain):
        for base in _base_urls(host):
            try:
                final_home, home_html = fetch_html(session, f"{base}/", follow_meta_refresh=True)
            except Exception as exc:  # noqa: BLE001
                result.notes.append(f"home {base}: {exc}")
                continue
            if len(home_html) < 500:
                continue
            home_url = home_url or final_home

            leader_urls = _links_from_html(home_html, final_home, LINK_LEADER_RE)
            appt_urls = _links_from_html(home_html, final_home, LINK_APPOINTMENT_RE)
            for key, path in known.items():
                url = path if path.startswith("http") else urljoin(final_home, path)
                if key == "leader_intro_url":
                    leader_urls.insert(0, url)
                elif key == "appointment_list_url":
                    appt_urls.insert(0, url)
            for path in LEADER_PATHS:
                leader_urls.append(f"{base.rstrip('/')}{path}")
            for path in APPOINTMENT_PATHS:
                appt_urls.append(f"{base.rstrip('/')}{path}")

            seen_leader: set[str] = set()
            for url in leader_urls:
                if url in seen_leader:
                    continue
                seen_leader.add(url)
                if delay:
                    time.sleep(delay)
                try:
                    final_url, score = _probe_leader(session, url, code, delay=delay)
                    if score > leader_best[1]:
                        leader_best = (final_url, score)
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"leader {url}: {exc}")

            seen_appt: set[str] = set()
            for url in appt_urls:
                if url in seen_appt:
                    continue
                seen_appt.add(url)
                if delay:
                    time.sleep(delay)
                try:
                    final_url, score = _probe_appointment(session, url)
                    if score > appt_best[1]:
                        appt_best = (final_url, score)
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"appt {url}: {exc}")

            if leader_best[1] or appt_best[1]:
                break
        if leader_best[1] or appt_best[1]:
            break

    result.home_url = home_url
    if leader_best[1]:
        result.leader_intro_url = leader_best[0]
        result.leader_count = leader_best[1]
    if appt_best[1]:
        result.appointment_list_url = appt_best[0]
        result.appointment_count = appt_best[1]
    return result


def discover_all(*, delay: float = 0.1, output: Path | None = None) -> list[ProvinceUrlDiscovery]:
    results: list[ProvinceUrlDiscovery] = []
    for _subdomain, _region, code in PROVINCE_SUBDOMAINS:
        if code == "shanghai":
            continue
        item = discover_province(code, _subdomain, delay=delay)
        results.append(item)
        if output is not None:
            output.write_text(
                json_dumps(results),
                encoding="utf-8",
            )
    return results


def json_dumps(results: list[ProvinceUrlDiscovery]) -> str:
    import json

    return json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2)
