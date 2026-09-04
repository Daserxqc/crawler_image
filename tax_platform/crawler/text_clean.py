"""Plain-text cleanup for scraped notice / list titles."""

from __future__ import annotations

import html
import re

_BR_RE = re.compile(r"(?i)<br\s*/?>")
_TAG_RE = re.compile(r"<[^>]+>")
# Page chrome glued onto titles by greedy get_text / broken CMS templates.
_CHROME_RE = re.compile(
    r"(?:"
    r"【\s*字体\s*[：:][^】]*】|"
    r"字体\s*[：:]\s*[小中大\s]+|"
    r"字号\s*[：:][^\s]*|"
    r"打印本页|关闭本页|分享到|复制链接|扫一扫|"
    r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s+\d{1,2}:\d{2}(?::\d{2})?"
    r")"
)
# Trailing decision/publish date often appended to list or H1 text.
_TRAILING_DECISION_DATE_RE = re.compile(
    r"[\s\u3000]*[（(]\s*"
    r"(?:"
    r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|"
    r"20\d{2}\s*年\s*\d{1,2}\s*月|"
    r"\d{1,2}\s*月\s*\d{1,2}\s*日|"
    r"20\d{2}-\d{1,2}-\d{1,2}"
    r")"
    r"\s*[）)]\s*$"
)
_TRAILING_BARE_DATETIME_RE = re.compile(
    r"[\s\u3000]+20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*$"
)
# Real 发文字号, e.g. 沪税任〔2026〕117号 / 沪税浦委任〔2026〕32号
_DOC_NO_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9、]{1,24}[〔\[]\d{4}[〕\]]\s*\d+\s*号)"
)
_DOC_NO_CHROME_MARKERS = (
    "字号",
    "打印",
    "微信",
    "扫一扫",
    "分享",
    "发布时间",
    "发文时间",
    "发布日期",
    "发文日期",
    "发文机关",
    "来源",
    "有效性",
    "[大]",
    "[中]",
    "[小]",
    "正文下载",
    "下载本页",
    "任免工作人员",
)


def scrub_notice_title(title: str | None) -> str:
    """Strip HTML/chrome and collapse whitespace; keep trailing decision dates."""
    if not title:
        return ""
    text = _BR_RE.sub("", str(title))
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _CHROME_RE.sub(" ", text)
    text = _TRAILING_BARE_DATETIME_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" ·\u3000")


def normalize_notice_title(title: str | None, *, keep_decision_date: bool = False) -> str:
    """Display/storage title: scrub chrome; by default also drop trailing ``（日期）``."""
    text = scrub_notice_title(title)
    if not keep_decision_date:
        text = _TRAILING_DECISION_DATE_RE.sub("", text).strip(" ·\u3000")
    return text


def normalize_doc_no(raw: str | None) -> str | None:
    """Keep only a plausible 发文字号; drop page-chrome blobs mis-stored as doc_no."""
    if not raw:
        return None
    text = re.sub(r"\s+", "", str(raw).strip())
    if not text:
        return None
    match = _DOC_NO_RE.search(text)
    if match:
        candidate = match.group(1)
        if len(candidate) <= 40 and not any(m in candidate for m in ("打印", "微信", "字号")):
            return candidate
    if len(text) > 40:
        return None
    if any(marker in text for marker in _DOC_NO_CHROME_MARKERS):
        return None
    if text.endswith("号") and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9〔〕\[\]\d]{4,40}", text):
        return text
    return None


_BODY_START_MARKERS = (
    "决定，任命",
    "决定:任命",
    "决定：任命",
    "决定，任命：",
    "决定：任命：",
    "研究决定，任命",
    "研究决定:任命",
    "研究决定：任命",
    "决定，免去",
    "决定：免去",
    "任命：",
    "任命:",
)

# Site chrome often glued after the decision block in stored raw_text.
_BODY_END_MARKERS = (
    "【打印】",
    "【下载】",
    "中国政府网",
    "国家税务总局各省级税务局",
    "访问统计",
    "网站管理",
    "友情链接",
    "扫一扫",
    "分享到",
    "直属单位",
    "税务相关",
)


def display_notice_body(raw: str | None) -> str:
    """Strip list/nav chrome from stored notice text for archive display."""
    if not raw:
        return ""
    text = html.unescape(str(raw))
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[\r\t]+", " ", text)
    text = re.sub(r"[ \u3000]+", " ", text).strip()

    start = -1
    for marker in _BODY_START_MARKERS:
        idx = text.find(marker)
        if idx != -1 and (start == -1 or idx < start):
            start = idx
    if start != -1:
        text = text[start:]

    end = -1
    for marker in _BODY_END_MARKERS:
        idx = text.find(marker)
        if idx != -1 and (end == -1 or idx < end):
            end = idx
    if end != -1:
        text = text[:end]

    text = text.strip(" \n\t；;，,|")
    # One clause per line for readability.
    text = re.sub(r"[；;]\s*", "；\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
