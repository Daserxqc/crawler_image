"""Parse personnel-appointment list pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import resolve_list_child_url
from tax_platform.crawler.text_clean import normalize_notice_title

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
CN_DATE_RE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日")
URL_ART_DATE_RE = re.compile(r"/art/(20\d{2})/(\d{1,2})/(\d{1,2})/")
# Fujian (and similar) notice files: …/t20260507_634732.htm
URL_T_FILE_DATE_RE = re.compile(r"/t(20\d{2})(\d{2})(\d{2})_\d+\.(?:s?html?|htm)\b", re.I)
SKIP_TITLE_KEYWORDS = ("招录", "招聘", "体检", "公示", "面试", "成绩", "采购", "招标", "征求意见")
KEEP_TITLE_KEYWORDS = ("任免", "任命", "任职", "免去", "免职")
# Nav crumbs that match KEEP_TITLE_KEYWORDS but are not notice titles
SKIP_EXACT_TITLES = {"人事任免", "人事信息", "任免", "任职信息", "人事管理"}
LIST_PATH_SUFFIXES = (
    "/rsrm",
    "/rsxx",
    "/jgrs",
    "/rsgl",
    "/ldjj",
    "/ldzl",
)
LIST_TITLE_RE = re.compile(r"共\s*\d+\s*条")


@dataclass(frozen=True)
class AppointmentListItem:
    title: str
    source_url: str
    published_on: date | None


def parse_appointment_list(html: str, list_url: str) -> list[AppointmentListItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[AppointmentListItem] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        raw_text = normalize_notice_title(anchor.get_text(" ", strip=True)).strip(" ·")
        # Jilin xxgk search truncates link text ("…20..."); full title is in title=.
        attr_title = normalize_notice_title((anchor.get("title") or "").strip()).strip(" ·")
        title = attr_title if (attr_title and (not raw_text or raw_text.endswith("...") or "…" in raw_text)) else raw_text
        if not title:
            title = raw_text or attr_title
        href = anchor.get("href") or ""
        if not title or not href or href.startswith("javascript:"):
            continue
        if not _is_appointment_title(title, href):
            continue
        source_url = resolve_list_child_url(list_url, href)
        if is_appointment_list_url(source_url, list_url=list_url):
            continue
        if source_url in seen:
            continue
        seen.add(source_url)
        parent_text = normalize_notice_title(anchor.parent.get_text(" ", strip=True) if anchor.parent else title)
        # Prefer row text (includes 发布日期 column) when present.
        row = anchor.find_parent("tr")
        row_text = normalize_notice_title(row.get_text(" ", strip=True)) if row else parent_text
        # Prefer article URL date (/art/YYYY/M/D/) — matches list-page publish order —
        # then title / row text (Chinese or ISO).
        published = _extract_date(source_url, title, row_text, parent_text)
        # Hubei-style date-only anchors: synthesize a usable title AFTER date strip.
        display_title = _strip_trailing_date(title)
        if DATE_RE.fullmatch(title.strip()):
            display_title = f"人事任免（{published.isoformat()}）" if published else "人事任免"
        items.append(
            AppointmentListItem(
                title=normalize_notice_title(display_title),
                source_url=source_url,
                published_on=published,
            )
        )
    return items


def _is_appointment_title(title: str, href: str = "") -> bool:
    # Hebei XXGK lists: every row title is literally「人事任免」; href is the notice.
    if title.strip() in SKIP_EXACT_TITLES:
        if title.strip() == "人事任免" and re.search(
            r"/t\d{8}_\d+\.(?:s?html?|htm)$", href, re.I
        ):
            return True
        return False
    if LIST_TITLE_RE.search(title):
        return False
    if any(keyword in title for keyword in SKIP_TITLE_KEYWORDS):
        return False
    if any(keyword in title for keyword in KEEP_TITLE_KEYWORDS):
        return True
    # Hubei (and similar): list rows show only the date; href points at notice body.
    if DATE_RE.fullmatch(title.strip()) and re.search(
        r"/(?:rsrm|rsxx|rsgl)(?:/|$).*\.(?:s?html?|htm)", href, re.I
    ):
        return True
    if DATE_RE.fullmatch(title.strip()) and re.search(r"/\d{5,}\.htm", href, re.I):
        return True
    return False


def is_appointment_list_url(source_url: str, *, list_url: str | None = None) -> bool:
    """True when *source_url* is a栏目列表页, not a detail notice."""
    if list_url and _url_key(source_url) == _url_key(list_url):
        return True
    from urllib.parse import urlparse

    path = urlparse(source_url).path.rstrip("/").lower()
    if not path:
        return True
    if any(path.endswith(suffix) for suffix in LIST_PATH_SUFFIXES):
        return True
    # Bare section folders without article file
    if re.search(r"/(?:rsrm|rsxx|jgrs|rsgl)/?$", path):
        return True
    return False


def _is_list_page_url(source_url: str, list_url: str) -> bool:
    """Backward-compatible alias."""
    return is_appointment_list_url(source_url, list_url=list_url)


def _url_key(url: str) -> str:
    """Host + path, ignoring http/https differences common in saved HTML."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _strip_trailing_date(title: str) -> str:
    return DATE_RE.sub("", title).strip(" ·")


def _extract_date(*texts: str) -> date | None:
    # Prefer article URL dates over title paren dates (Fujian 发文日期 vs 任免日).
    for text in texts:
        if not text:
            continue
        art = URL_ART_DATE_RE.search(text)
        if art:
            return date(int(art.group(1)), int(art.group(2)), int(art.group(3)))
        tfile = URL_T_FILE_DATE_RE.search(text)
        if tfile:
            return date(int(tfile.group(1)), int(tfile.group(2)), int(tfile.group(3)))
    for text in texts:
        if not text:
            continue
        match = DATE_RE.search(text)
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
        cn = CN_DATE_RE.search(text)
        if cn:
            return date(int(cn.group(1)), int(cn.group(2)), int(cn.group(3)))
    return None
