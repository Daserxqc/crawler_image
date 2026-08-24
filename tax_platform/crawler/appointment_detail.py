"""Extract metadata and body text from an appointment notice page."""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from tax_platform.models.entities import NoticeMeta

STOP_LABELS = ("发文单位", "发文日期", "索引号", "主题分类", "名称", "经研究", "决定")
LABEL_ISSUED_ON = re.compile(r"发文日期[:：]\s*(20\d{2}-\d{2}-\d{2})")
LABEL_PUBLISHED = re.compile(r"发布时间[:：]\s*(20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)")


def parse_appointment_detail(html: str, source_url: str, bureau_code: str) -> NoticeMeta:
    soup = BeautifulSoup(html, "html.parser")
    title = _page_title(soup)
    text = _visible_text(soup)
    issued_on = _parse_date(_search(LABEL_ISSUED_ON, text))
    published_raw = _search(LABEL_PUBLISHED, text)
    published_at = _parse_datetime(published_raw) if published_raw else None
    body = _extract_body(text, title)
    return NoticeMeta(
        bureau_code=bureau_code,
        title=title,
        source_url=source_url,
        published_at=published_at,
        doc_no=_value_after(text, "文号"),
        issuer=_value_after(text, "发文单位"),
        issued_on=issued_on,
        raw_text=body,
    )


def _page_title(soup: BeautifulSoup) -> str:
    heading = soup.select_one("h1, h2, .title, .bt")
    if heading:
        title = heading.get_text(" ", strip=True)
        if title:
            return title
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return page_title.split("|")[0].strip()


def _visible_text(soup: BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _value_after(text: str, label: str) -> str | None:
    match = re.search(rf"{label}[:：]\s*", text)
    if not match:
        return None
    rest = text[match.end() :]
    cut = len(rest)
    for stop in STOP_LABELS:
        if stop == label:
            continue
        index = rest.find(stop)
        if index != -1:
            cut = min(cut, index)
    value = re.sub(r"\s+", "", rest[:cut]).strip("：:，,")
    return value or None


def _search(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _parse_datetime(value: str) -> datetime:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})(?:\s*)?(\d{2}:\d{2})?", value)
    if not match:
        raise ValueError(f"unrecognized datetime: {value!r}")
    if match.group(2):
        return datetime.strptime(f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M")
    return datetime.strptime(match.group(1), "%Y-%m-%d")


def _extract_body(text: str, title: str) -> str:
    start = text.find(title) if title else -1
    chunk = text[start:] if start >= 0 else text
    for marker in ("各单位", "经研究", "决定：", "决定:"):
        index = chunk.find(marker)
        if index != -1:
            chunk = chunk[index:]
            break
    end_tokens = ("扫一扫", "网站地图", "主办单位", "京ICP", "沪ICP")
    ends = [chunk.find(token) for token in end_tokens if chunk.find(token) != -1]
    if ends:
        chunk = chunk[: min(ends)]
    return chunk.strip()
