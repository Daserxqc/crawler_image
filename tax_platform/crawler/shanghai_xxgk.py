"""Shanghai sub-bureau rsrm lists via xxgk.xml (HTML hub often 403)."""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree as ET

import requests

from tax_platform.crawler.appointment_list import AppointmentListItem


def _cdata_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return (node.text or "").strip()


def _parse_pub_date(text: str) -> date | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def items_from_xxgk_xml(list_url: str, *, session: requests.Session) -> list[AppointmentListItem]:
    """Load rsrm column from ``.../rsrm/xxgk.xml`` (+ paginated ``xxgk_N.xml``)."""
    base = (list_url or "").rstrip("/")
    if "/rsrm" in base:
        base = base.split("/rsrm")[0] + "/rsrm"
    elif not base.endswith("/rsrm"):
        return []
    base = base.replace("http://", "https://")
    items: list[AppointmentListItem] = []
    seen: set[str] = set()
    page = 0
    while page < 30:
        name = "xxgk.xml" if page == 0 else f"xxgk_{page}.xml"
        url = f"{base}/{name}"
        try:
            resp = session.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
                    ),
                    "Referer": list_url or base + "/",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
        except requests.RequestException:
            break
        if resp.status_code != 200 or b"<RECS" not in resp.content[:200]:
            break
        root = ET.fromstring(resp.content)
        recs = root.findall("REC")
        if not recs:
            break
        for rec in recs:
            title = _cdata_text(rec.find("TITLE"))
            href = _cdata_text(rec.find("URL")).replace("http://", "https://")
            pub = _parse_pub_date(_cdata_text(rec.find("DOCRELTIME")))
            if not title or not href or href in seen:
                continue
            seen.add(href)
            items.append(AppointmentListItem(title=title, source_url=href, published_on=pub))
        page_count = int(_cdata_text(root.find("PAGECOUNT")) or "1")
        page += 1
        if page >= page_count:
            break
    return items


def fetch_shanghai_rsrm_list_html(session: requests.Session, list_url: str) -> str | None:
    """Synthetic HTML for ``parse_appointment_list`` when rsrm hub returns 403."""
    if "shanghai.chinatax.gov.cn" not in list_url or "/rsrm" not in list_url:
        return None
    items = items_from_xxgk_xml(list_url, session=session)
    if not items:
        return None
    links = []
    for item in items:
        pub = item.published_on.isoformat() if item.published_on else ""
        extra = f' data-date="{pub}"' if pub else ""
        safe_title = re.sub(r"[<>\"]", "", item.title)
        links.append(f'<a href="{item.source_url}"{extra}>{safe_title}</a>')
    return f"<html><body>{''.join(links)}</body></html>"
