"""Parse personnel-appointment list pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import resolve_list_child_url

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
SKIP_TITLE_KEYWORDS = ("招录", "招聘", "体检", "公示", "面试", "成绩")
KEEP_TITLE_KEYWORDS = ("任免", "任职", "免去", "免职")


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
        if not _is_appointment_title(title):
            continue
        source_url = resolve_list_child_url(list_url, href)
        if source_url in seen:
            continue
        seen.add(source_url)
        items.append(
            AppointmentListItem(
                title=_strip_trailing_date(title),
                source_url=source_url,
                published_on=_extract_date(title, anchor.parent.get_text(" ", strip=True) if anchor.parent else title),
            )
        )
    return items


def _is_appointment_title(title: str) -> bool:
    if any(keyword in title for keyword in SKIP_TITLE_KEYWORDS):
        return False
    return any(keyword in title for keyword in KEEP_TITLE_KEYWORDS)


def _strip_trailing_date(title: str) -> str:
    return DATE_RE.sub("", title).strip(" ·")


def _extract_date(*texts: str) -> date | None:
    for text in texts:
        match = DATE_RE.search(text or "")
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    return None
