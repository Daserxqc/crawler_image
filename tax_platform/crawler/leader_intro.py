"""Parse tax-bureau leader introduction pages."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import extract_meta_refresh_url, resolve_list_child_url
from tax_platform.models.entities import LeaderDuty

PROFILE_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5·]{2,4})，(?P<gender>男|女)，(?P<ethnicity>[^，。]*族)，(?P<title>[^。]+)。"
)
ARTICLE_HREF_RE = re.compile(r"(?:20\d{4}/t\d+|t\d+)\.s?html|/ld_\d+", re.IGNORECASE)
SKIP_LINK_TEXT = ("首页", "信息公开", "网站地图", "返回", "打印")


def parse_leader_intro(html: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    text = _visible_text(html)
    matches = list(PROFILE_RE.finditer(text))
    duties: list[LeaderDuty] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        duty_summary, departments = _duty_and_departments(block)
        duties.append(
            LeaderDuty(
                person_name=match.group("name"),
                gender=match.group("gender"),
                ethnicity=match.group("ethnicity"),
                title_raw=match.group("title").strip(),
                duty_summary=duty_summary,
                departments_raw=departments,
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def leader_page_targets(html: str, hub_url: str) -> list[str]:
    """Follow a META REFRESH hub, or collect article links from a list page."""
    refresh_url = extract_meta_refresh_url(html, hub_url)
    if refresh_url:
        return [refresh_url]
    urls: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True))
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:") or title in SKIP_LINK_TEXT:
            continue
        if not ARTICLE_HREF_RE.search(href):
            continue
        url = resolve_list_child_url(hub_url, href)
        if url in seen or url.rstrip("/") == hub_url.rstrip("/"):
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _duty_and_departments(block: str) -> tuple[str | None, list[str]]:
    if "主持全面工作" in block:
        return "主持全面工作", []
    if "分管工作" in block or "分管" in block:
        return "分管工作", _split_departments(block)
    return None, []


def _split_departments(block: str) -> list[str]:
    chunk = block
    for marker in ("分管工作", "分管"):
        index = chunk.find(marker)
        if index != -1:
            chunk = chunk[index + len(marker) :]
            break
    chunk = re.sub(r"^[\s：:，,]*分管", "", chunk.strip())
    chunk = chunk.strip("：:。；;，, ")
    parts = [part.strip("。；;，, ") for part in re.split(r"[、]", chunk)]
    return [part for part in parts if part and part not in {"分管工作", "主持全面工作"}]
