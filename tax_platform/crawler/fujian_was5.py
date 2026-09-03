"""Fujian city xxgk lists via WAS5 search (jgsz shell has no list rows)."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from urllib.parse import quote, urljoin, urlparse

import requests

_CHANNELID_RE = re.compile(r"channelid\s*[:=]\s*[\"']?(\d+)", re.I)
# zTree node: name/t 人事任免 … id / file nearby (order varies).
_RS_NODE_RE = re.compile(
    r"\{[^{}]{0,400}(?:人事任免|rsxx_\d+|/(?:rsrm|rsxx)/)[^{}]{0,400}\}",
    re.I,
)
_NODE_ID_RE = re.compile(r"\bid\s*:\s*[\"'](\d+)[\"']", re.I)
_NODE_FILE_RE = re.compile(r"\bfile\s*:\s*[\"']([^\"']+)[\"']", re.I)
_DOC_BLOCK_RE = re.compile(r"\{\s*\"title\"\s*:", re.I)


def _parse_day(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _extract_channelid(html: str) -> str | None:
    m = _CHANNELID_RE.search(html or "")
    return m.group(1) if m else None


def _extract_rs_chnlid_and_file(html: str) -> tuple[str | None, str | None]:
    """Return (chnlid, file_url) for the 人事任免 zTree node."""
    html = html or ""
    best: tuple[str | None, str | None] = (None, None)
    for block in _RS_NODE_RE.findall(html):
        if "人事任免" not in block and not re.search(r"rsxx_\d+|/(?:rsrm|rsxx)/", block, re.I):
            continue
        nid = _NODE_ID_RE.search(block)
        nfile = _NODE_FILE_RE.search(block)
        chnlid = nid.group(1) if nid else None
        file_url = nfile.group(1) if nfile else None
        if "人事任免" in block and chnlid:
            return chnlid, file_url
        if chnlid and best[0] is None:
            best = (chnlid, file_url)
    return best


def _sanitize_was5_json(raw: str) -> str:
    """WAS5 returns almost-JSON with py/dy single-quoted placeholders."""
    text = (raw or "").strip()
    text = re.sub(r"('###[^']*###')", '""', text)
    return text


def _docs_from_was5(raw: str) -> list[dict]:
    text = _sanitize_was5_json(raw)
    try:
        data = json.loads(text)
        docs = data.get("docs") or []
        if isinstance(docs, list):
            return [d for d in docs if isinstance(d, dict)]
    except json.JSONDecodeError:
        logging.debug("fujian was5 json.loads failed; falling back to regex")
    docs: list[dict] = []
    for m in re.finditer(
        r"\{\s*\"title\"\s*:\s*\"((?:\\.|[^\"])*)\"[\s\S]*?\"url\"\s*:\s*\"((?:\\.|[^\"])*)\""
        r"[\s\S]*?\"time\"\s*:\s*\"((?:\\.|[^\"])*)\"",
        raw,
    ):
        title = bytes(m.group(1), "utf-8").decode("unicode_escape") if "\\" in m.group(1) else m.group(1)
        docs.append({"title": title, "url": m.group(2), "time": m.group(3)})
    return docs


def _was5_search_url(origin: str, channelid: str, chnlid: str, *, page: int = 1, prepage: int = 20) -> str:
    classsql = quote(f"modal=1,2,3*chnlid={chnlid}", safe="")
    sortfield = quote("-docorderpri,-docreltime", safe="")
    return (
        f"{origin}/was5/web/search?channelid={channelid}"
        f"&sortfield={sortfield}&classsql={classsql}"
        f"&prepage={prepage}&page={page}"
    )


def fetch_fujian_was5_list_html(
    session: requests.Session,
    list_url: str,
    page_html: str | None = None,
) -> str | None:
    """
    Fujian city 主动公开目录 shells (jgsz/ etc.) load list rows via Avalon→WAS5 search.

    Without this, HTTP/browser only sees the shell and hangs trying zTree clicks.
    """
    if "fujian.chinatax.gov.cn" not in (list_url or ""):
        return None
    if "/zfxxgkml/" not in list_url and "/was5/" not in list_url:
        return None

    html = page_html or ""
    if not html:
        try:
            resp = session.get(
                list_url,
                timeout=25,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                    "Referer": list_url,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            html = resp.text or ""
        except requests.RequestException as exc:
            logging.debug("fujian was5 shell fetch failed: %s", exc)
            return None

    channelid = _extract_channelid(html)
    chnlid, file_url = _extract_rs_chnlid_and_file(html)
    if not channelid or not chnlid:
        logging.debug(
            "fujian was5 missing ids for %s (channelid=%s chnlid=%s)",
            list_url,
            channelid,
            chnlid,
        )
        return None

    parsed = urlparse(list_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Prefer https for search endpoint when shell was http.
    if origin.startswith("http://"):
        origin = "https://" + origin[len("http://") :]

    links: list[str] = []
    seen: set[str] = set()
    page = 1
    max_pages = 5
    while page <= max_pages:
        search_url = _was5_search_url(origin, channelid, chnlid, page=page, prepage=20)
        try:
            resp = session.get(
                search_url,
                timeout=25,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                    "Referer": list_url,
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
        except requests.RequestException as exc:
            logging.debug("fujian was5 search failed page=%s: %s", page, exc)
            break
        if resp.status_code != 200 or not resp.text:
            break
        docs = _docs_from_was5(resp.text)
        if not docs:
            break
        for doc in docs:
            title = (doc.get("title") or doc.get("title2") or "").strip()
            href = (doc.get("url") or doc.get("chnldocurl") or "").strip()
            if href.startswith("http://fujian.chinatax.gov.cn"):
                href = "https://" + href[len("http://") :]
            pub = _parse_day(str(doc.get("time") or doc.get("pubdate") or ""))
            if not title or not href or href in seen:
                continue
            if not any(k in title for k in ("任免", "任命", "任职", "免去", "免职")):
                continue
            seen.add(href)
            extra = f' data-date="{pub.isoformat()}"' if pub else ""
            safe = re.sub(r"[<>\"]", "", title)
            # Include ISO date in row text for parse_appointment_list date extraction.
            date_txt = pub.isoformat() if pub else ""
            links.append(
                f'<tr><td><a href="{href}"{extra}>{safe}</a></td><td>{date_txt}</td></tr>'
            )
        # Stop after first page for list-head monitoring (newest first).
        break

    if not links:
        return None
    base = file_url or list_url
    logging.info(
        "Fujian WAS5 list OK (%s docs) channelid=%s chnlid=%s",
        len(links),
        channelid,
        chnlid,
    )
    return f"<html><body><!-- base:{base} --><table>{''.join(links)}</table></body></html>"
