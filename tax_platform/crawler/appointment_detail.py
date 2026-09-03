"""Extract metadata and body text from an appointment notice page."""

from __future__ import annotations

import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from tax_platform.models.entities import NoticeMeta
from tax_platform.crawler.text_clean import normalize_doc_no, normalize_notice_title, scrub_notice_title

STOP_LABELS = (
    "发文单位",
    "发文机关",
    "发文日期",
    "成文日期",
    "发布日期",
    "发布时间",
    "发文时间",
    "索引号",
    "主题分类",
    "名称",
    "标题",
    "字号",
    "字体",
    "来源",
    "信息来源",
    "有效性",
    "公开方式",
    "浏览次数",
    "经研究",
    "决定",
)
LABEL_ISSUED_ON = re.compile(r"发文日期[:：]\s*(20\d{2}-\d{2}-\d{2})")
LABEL_PUBLISH_DATE = re.compile(r"发布日期[:：]\s*(20\d{2}-\d{2}-\d{2})")
LABEL_PUBLISHED = re.compile(
    r"发布(?:时间|日期)[:：]\s*(20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)"
)
# 标题里的任免决定日，如「…任免工作人员（2025年12月11日）」
TITLE_DECISION_DAY = re.compile(
    r"[（(]\s*(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*[）)]"
)


def parse_date_from_title(title: str | None) -> date | None:
    """Extract the decision date embedded in notice titles (most factual for 变更日期)."""
    if not title:
        return None
    match = TITLE_DECISION_DAY.search(title)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def resolve_issued_on(
    *,
    title: str | None,
    page_text: str = "",
    meta_pubdate: str | None = None,
    published_at: datetime | None = None,
) -> date | None:
    """Pick the factual notice/decision day.

    Priority:
    1. Decision date in title ``（YYYY年M月D日）`` — what users see as 任免日
    2. 发文日期
    3. 发布日期 / 发布时间
    4. meta PubDate (often CMS move date; last resort)
    5. published_at already parsed
    """
    titled = parse_date_from_title(title)
    if titled is not None:
        return titled
    for pattern in (LABEL_ISSUED_ON, LABEL_PUBLISH_DATE, LABEL_PUBLISHED):
        raw = _search(pattern, page_text)
        day = _parse_date(raw)
        if day is not None:
            return day
    if meta_pubdate:
        day = _parse_date(meta_pubdate)
        if day is not None:
            return day
    if published_at is not None:
        return published_at.date()
    return None


def parse_appointment_detail(html: str, source_url: str, bureau_code: str) -> NoticeMeta:
    soup = BeautifulSoup(html, "html.parser")
    # Keep decision date in title long enough to resolve issued_on, then strip for storage.
    title_raw = _page_title(soup)
    text = _visible_text(soup)
    content_node = soup.select_one("#zoom, .contentmain, .TRS_Editor, .article_content, #content")
    content_text = _visible_text(content_node) if content_node is not None else ""
    published_raw = _search(LABEL_PUBLISHED, text) or _search(LABEL_PUBLISH_DATE, text)
    published_at = _parse_datetime(published_raw) if published_raw else None
    meta_pub = soup.select_one('meta[name="PubDate"]')
    meta_pubdate = str(meta_pub.get("content")) if meta_pub and meta_pub.get("content") else None
    issued_on = resolve_issued_on(
        title=title_raw,
        page_text=text,
        meta_pubdate=meta_pubdate,
        published_at=published_at,
    )
    if published_at is None and issued_on is not None:
        published_at = datetime.combine(issued_on, datetime.min.time())
    body_source = content_text or text
    body = _extract_body(body_source, title_raw)
    issuer = _value_after(text, "发文单位")
    if not issuer:
        src = soup.select_one('meta[name="ContentSource"]')
        if src and src.get("content"):
            issuer = str(src.get("content")).strip() or None
    return NoticeMeta(
        bureau_code=bureau_code,
        title=normalize_notice_title(title_raw),
        source_url=source_url,
        published_at=published_at,
        doc_no=_extract_doc_no(text),
        issuer=issuer,
        issued_on=issued_on,
        raw_text=body,
    )


def _extract_doc_no(text: str) -> str | None:
    """Pull a real 发文字号; never keep 字号/分享 chrome blobs."""
    for label in ("发文字号", "文号"):
        cleaned = normalize_doc_no(_value_after(text, label))
        if cleaned:
            return cleaned
    # Some templates put the 文号 mid-line without a clean stop boundary.
    match = re.search(
        r"(?:发文字号|文\s*号)\s*[:：]\s*([\u4e00-\u9fa5A-Za-z0-9〔〕\[\]\d]{4,40}?号)",
        text,
    )
    if match:
        return normalize_doc_no(match.group(1))
    return None


def _page_title(soup: BeautifulSoup) -> str:
    for selector in ("#tit_name", "h1#tit_name", "h1.title", ".contentbox h1"):
        heading = soup.select_one(selector)
        if heading:
            title = scrub_notice_title(heading.get_text(" ", strip=True))
            if title and "信息公开" not in title:
                return title
    meta = soup.select_one('meta[name="ArticleTitle"]')
    if meta and meta.get("content"):
        return scrub_notice_title(str(meta.get("content")))
    heading = soup.select_one("h1, .title, .bt")
    if heading:
        title = scrub_notice_title(heading.get_text(" ", strip=True))
        if title and "信息公开" not in title:
            return title
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return scrub_notice_title(page_title.split("|")[0])


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
