"""Helpers for 政府信息公开 (xxgk) AJAX list columns (Zhejiang style)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup


def find_xxgk_load_call(html: str) -> tuple[str, str, str, str] | None:
    """Return (path_with_query, divid, cid, webid) from loadDynamic(...) if present."""
    # Fully static first argument
    simple = re.search(
        r"loadDynamic\(\s*['\"]([^'\"]*xxgk/search\.jsp[^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,"
        r"\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]",
        html,
        re.I,
    )
    if simple and "+" not in simple.group(0).split(",")[0]:
        return simple.group(1), simple.group(2), simple.group(3), simple.group(6)

    # Zhejiang concat style:
    # loadDynamic('/module/xxgk/search.jsp?infotypeId=Z2401'+a+'&vc_title='+encodeURI(b)+'&vc_number='+encodeURI(c)+'&area=AREA',
    #             'divID', '0','1000','UID','WEBID', ...)
    concat = re.search(
        r"loadDynamic\(\s*['\"](/module/xxgk/search\.jsp\?infotypeId=[A-Za-z0-9]+)['\"]"
        r".*?area=([0-9A-Za-z]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*,"
        r"\s*['\"][^'\"]*['\"]\s*,\s*['\"][^'\"]*['\"]\s*,\s*['\"]([^'\"]*)['\"]",
        html,
        re.I | re.S,
    )
    if concat:
        path = f"{concat.group(1)}&vc_title=&vc_number=&area={concat.group(2)}"
        return path, concat.group(3), concat.group(4), concat.group(5)
    return None


def fetch_xxgk_list_html(session, page_url: str, page_html: str) -> str | None:
    """POST the xxgk search.jsp endpoint the same way loadDynamic does."""
    call = find_xxgk_load_call(page_html)
    if call is None:
        return None
    path_with_query, divid, cid, webid = call
    ajax_url = urljoin(page_url, path_with_query)
    parsed = urlparse(ajax_url)
    # Mirror dynamic.js when URL already contains area=
    data = (
        f"infotypeId={cid}&jdid={webid}&divid={divid}"
        f"&vc_title=&vc_number=&vc_filenumber=&vc_all=&texttype=&fbtime="
        f"&{parsed.query}"
    )
    response = session.post(
        ajax_url,
        data=data,
        headers={
            "Referer": page_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=20,
        verify=False,
    )
    if response.status_code >= 400:
        return None
    encodings = [response.apparent_encoding, "utf-8", "gb18030"]
    html = ""
    for encoding in encodings:
        if not encoding:
            continue
        try:
            html = response.content.decode(encoding, errors="ignore")
            break
        except LookupError:
            continue
    if not html or len(BeautifulSoup(html, "html.parser").select("a[href]")) < 1:
        return None
    return html
