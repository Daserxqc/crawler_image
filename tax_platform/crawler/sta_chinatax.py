"""National STA (总局) 人事任免 list via getFileListByCodeId API."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests

LIST_URL = "https://www.chinatax.gov.cn/chinatax/n810214/c102374/c102384/n810611r/"
API_URL = "https://www.chinatax.gov.cn/getFileListByCodeId"
CHANNEL_ID = "aea711f23a3b4592a23a538276bbdf94"
PAGE_SIZE = 20


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("http://www.chinatax.gov.cn"):
        return "https://www.chinatax.gov.cn" + url[len("http://www.chinatax.gov.cn") :]
    return url


def _is_appt_title(title: str) -> bool:
    if "年报" in title or "政府信息公开" in title:
        return False
    if "任免工作人员" in title:
        return True
    return "任免" in title and "工作人员" in title


def fetch_sta_list_html(session: requests.Session, list_url: str | None = None) -> str | None:
    """Synthetic list HTML from STA pagination API (list page uses layui JS only)."""
    if "chinatax.gov.cn" not in (list_url or LIST_URL):
        return None
    base = list_url or LIST_URL
    if "n810611r" not in base and "/c102384/" not in base:
        return None

    headers = {
        "Referer": base,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.chinatax.gov.cn",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        ),
    }
    links: list[str] = []
    seen: set[str] = set()
    for page in range(1, 6):
        data = {
            "codeId": "",
            "channelId": CHANNEL_ID,
            "keySYH": "syh",
            "keyFWZH": "fwzh",
            "keyTc": "ticai",
            "relateSubChannels": "true",
            "page": str(page),
            "size": str(PAGE_SIZE),
        }
        try:
            resp = session.post(API_URL, data=data, headers=headers, timeout=30, verify=False)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            logging.debug("sta list API page=%s failed: %s", page, exc)
            break
        rows = ((payload or {}).get("results") or {}).get("data", {}).get("results") or []
        if not rows:
            break
        for row in rows:
            title = re.sub(r"<[^>]+>", "", (row.get("title") or row.get("titleHtml") or "")).strip()
            title = re.sub(r"\s+", " ", title)
            href = _normalize_url(str(row.get("url") or row.get("redirectUrl") or ""))
            pub = _parse_day(row.get("publishedTimeStr"))
            if not title or not href or href in seen or not _is_appt_title(title):
                continue
            seen.add(href)
            extra = f' data-date="{pub.isoformat()}"' if pub else ""
            safe = re.sub(r"[<>\"]", "", title)
            date_txt = pub.isoformat() if pub else ""
            links.append(
                f'<tr><td><a href="{href}"{extra}>{safe}</a></td><td>{date_txt}</td></tr>'
            )
        total = int(((payload or {}).get("results") or {}).get("data", {}).get("total") or 0)
        if page * PAGE_SIZE >= total or len(rows) < PAGE_SIZE:
            break

    if not links:
        return None
    logging.info("STA list API OK (%s docs)", len(links))
    return f"<html><body><table>{''.join(links)}</table></body></html>"
