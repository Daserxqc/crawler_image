"""Return only 领导专栏 person-name column URLs (not site nav)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import resolve_list_child_url
from tax_platform.crawler.leader_intro import PERSON_NAME_ONLY_RE, _clean_name

LEADER_COL_HREF_RE = re.compile(r"/col/col\d+/", re.I)
SKIP = {"首页", "信息公开", "新闻动态", "政策文件", "纳税服务", "互动交流", "领导专栏", "机构职能"}


def leader_column_person_targets(html: str, hub_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:"):
            continue
        if not LEADER_COL_HREF_RE.search(href):
            continue
        name = _clean_name(anchor.get_text(" ", strip=True))
        if name in SKIP or not PERSON_NAME_ONLY_RE.fullmatch(name):
            continue
        if not (2 <= len(name) <= 8):
            continue
        url = resolve_list_child_url(hub_url, href)
        if url in seen or url.rstrip("/") == hub_url.rstrip("/"):
            continue
        seen.add(url)
        urls.append(url)
    return urls
