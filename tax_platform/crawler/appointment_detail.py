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
    content_node = soup.select_one("#zoom, .contentmain, .TRS_Editor, .article_content, #content")
    content_text = _visible_text(content_node) if content_node is not None else ""
    issued_on = _parse_date(_search(LABEL_ISSUED_ON, text))
    meta_pub = soup.select_one('meta[name="PubDate"]')
    if issued_on is None and meta_pub and meta_pub.get("content"):
        issued_on = _parse_date(str(meta_pub.get("content")))
    published_raw = _search(LABEL_PUBLISHED, text)
    published_at = _parse_datetime(published_raw) if published_raw else None
    if published_at is None and issued_on is not None:
        published_at = datetime.combine(issued_on, datetime.min.time())
    body_source = content_text or text
    body = _extract_body(body_source, title)
    issuer = _value_after(text, "发文单位")
    if not issuer:
        src = soup.select_one('meta[name="ContentSource"]')
        if src and src.get("content"):
            issuer = str(src.get("content")).strip() or None
    return NoticeMeta(
        bureau_code=bureau_code,
        title=title,
        source_url=source_url,
        published_at=published_at,
        doc_no=_value_after(text, "文号") or _value_after(text, "发文字号"),
        issuer=issuer,
        issued_on=issued_on,
        raw_text=body,
    )


def _page_title(soup: BeautifulSoup) -> str:
    for selector in ("#tit_name", "h1#tit_name", "h1.title", ".contentbox h1"):
        heading = soup.select_one(selector)
        if heading:
            title = heading.get_text(" ", strip=True)
            if title and "信息公开" not in title:
                return title
    meta = soup.select_one('meta[name="ArticleTitle"]')
    if meta and meta.get("content"):
        return str(meta.get("content")).strip()
    heading = soup.select_one("h1, .title, .bt")
    if heading:
        title = heading.get_text(" ", strip=True)
        if title and "信息公开" not in title:
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
    value = value.strip()
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    # Fallback: first 10 chars as YYYY-MM-DD when already padded.
    try:
        return datetime.strptime(value[:10].strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime:
    match = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s*)?(\d{1,2}:\d{2})?", value)
    if not match:
        raise ValueError(f"unrecognized datetime: {value!r}")
    y, mo, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if match.group(4):
        hh, mm = match.group(4).split(":")
        return datetime(y, mo, d, int(hh), int(mm))
    return datetime(y, mo, d)


def _extract_body(text: str, title: str) -> str:
    start = text.find(title) if title else -1
    chunk = text[start:] if start >= 0 else text
    for marker in ("各单位", "经研究", "决定，任命", "决定：", "决定:", "任命：", "任命:"):
        index = chunk.find(marker)
        if index != -1:
            chunk = chunk[index:]
            break
    end_tokens = ("扫一扫", "网站地图", "主办单位", "京ICP", "沪ICP", "冀ICP", "[打印本页]", "[正文下载]")
    ends = [chunk.find(token) for token in end_tokens if chunk.find(token) != -1]
    if ends:
        chunk = chunk[: min(ends)]
    return chunk.strip()
