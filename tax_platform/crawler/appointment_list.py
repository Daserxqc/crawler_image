"""Parse personnel-appointment list pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import resolve_list_child_url

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
SKIP_TITLE_KEYWORDS = ("招录", "招聘", "体检", "公示", "面试", "成绩")
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
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip(" ·")
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
        published = _extract_date(title, anchor.parent.get_text(" ", strip=True) if anchor.parent else title)
        # Hubei-style date-only anchors: synthesize a usable title AFTER date strip.
        display_title = _strip_trailing_date(title)
        if DATE_RE.fullmatch(title.strip()):
            display_title = f"人事任免（{published.isoformat()}）" if published else "人事任免"
        items.append(
            AppointmentListItem(
                title=display_title,
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
    for text in texts:
        match = DATE_RE.search(text or "")
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    return None
