"""Helpers for 政府信息公开 (xxgk) AJAX list columns (Zhejiang / Shandong / Jilin style)."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

_TREE_IFRAME_RE = re.compile(
    r"""(?:src|SRC)\s*=\s*['"]([^'"]*xxgk/tree\.jsp\?[^'"]+)['"]""",
    re.I,
)
# HTML often encodes & as &amp; inside iframe src.
_AREA_RE = re.compile(r"(?:[?&]|&amp;)area=([0-9A-Za-z]+)", re.I)
_DIVID_RE = re.compile(r"(?:[?&]|&amp;)divid=(div\d+)", re.I)
# Tree nodes may use JS-escaped quotes: funclick(\'rsglrsrm\',\'...\')...">人事任免
_FUNCLICK_RE = re.compile(
    r"""funclick\(\s*\\?['"]([A-Za-z0-9]+)\\?['"]\s*,[^)]*\)\s*;?\s*\\?["']?\s*>\s*([^<]{1,40})""",
    re.I,
)
_FUNCLICK_LOOSE_RE = re.compile(
    r"""funclick\(\s*\\?['"]([A-Za-z0-9]+)\\?['"][^)]*\)[^<]{0,80}>\s*([^<]{1,40})""",
    re.I,
)
_LOAD_DYNAMIC_SD = re.compile(
    r"""loadDynamic\(\s*['"](/module/xxgk/search\.jsp\?(?:standardXxgk=\d+&)?infotypeId=)['"]\s*\+\s*[a-zA-Z_]+"""
    r""".*?area=([0-9A-Za-z]+)['"]\s*,\s*['"]([^'"]+)['"]"""
    r"""(?:\s*,\s*['"][^'"]*['"]){3}\s*,\s*['"]([^'"]*)['"]""",
    re.I | re.S,
)
# Jilin shells: loadDynamic('...&area=AREA', 'div4', ...) may include encodeURI(b) mid-arg.
_LOAD_DYNAMIC_AREA = re.compile(
    r"""loadDynamic\(\s*['"][^'"]*xxgk/search\.jsp[^'"]*area=([0-9A-Za-z]+)['"]"""
    r"""(?:\s*\+\s*[^,]+)*\s*,\s*['"]([^'"]+)['"]""",
    re.I | re.S,
)


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

    # Zhejiang / Jilin concat style:
    # loadDynamic('/module/xxgk/search.jsp?infotypeId=Z2401'+a+...&area=AREA', ...)
    # loadDynamic('/module/xxgk/search.jsp?standardXxgk=1&infotypeId='+a+...&area=AREA', 'div4', ...)
    concat = re.search(
        r"loadDynamic\(\s*['\"](/module/xxgk/search\.jsp\?(?:standardXxgk=\d+&)?infotypeId=)([A-Za-z0-9]*)['\"]"
        r".*?area=([0-9A-Za-z]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*,"
        r"\s*['\"][^'\"]*['\"]\s*,\s*['\"][^'\"]*['\"]\s*,\s*['\"]([^'\"]*)['\"]",
        html,
        re.I | re.S,
    )
    if concat:
        path = f"{concat.group(1)}{concat.group(2)}&vc_title=&vc_number=&area={concat.group(3)}"
        return path, concat.group(4), concat.group(5), concat.group(6)
    return None


def extract_xxgk_shell(html: str, page_url: str) -> dict[str, str] | None:
    """Pull area / divid / webid / tree URL from a statutory-disclosure shell page.

    Jilin city hubs pass the bureau id as ``?vc_xxgkarea=...`` (and often ``&number=``
    for the tree infotype). The list itself is loaded via ``/module/xxgk/search.jsp``.
    """
    area = None
    divid = None
    webid = "1"
    tree = None

    qs = parse_qs(urlparse(page_url).query)
    area = (qs.get("vc_xxgkarea") or qs.get("area") or [None])[0] or None

    iframe = _TREE_IFRAME_RE.search(html)
    if iframe:
        tree_raw = unescape(iframe.group(1))
        tree = urljoin(page_url, tree_raw)
        area_m = _AREA_RE.search(tree_raw)
        divid_m = _DIVID_RE.search(tree_raw)
        if area_m:
            area = area or area_m.group(1)
        if divid_m:
            divid = divid_m.group(1)

    sd = _LOAD_DYNAMIC_SD.search(html)
    if sd:
        area = area or sd.group(2)
        divid = divid or sd.group(3)
        webid = sd.group(4) or webid

    if not area or not divid:
        m = _LOAD_DYNAMIC_AREA.search(html)
        if m:
            area = area or m.group(1)
            divid = divid or m.group(2)

    # HTML body may embed vc_xxgkarea=... even when the request URL omitted it.
    if not area:
        m = re.search(r"vc_xxgkarea=([0-9A-Za-z]+)", html, re.I)
        if m:
            area = m.group(1)

    if area and not divid:
        divid = "div4"
    if area and not tree:
        tree = urljoin(
            page_url,
            f"/module/xxgk/tree.jsp?standardXxgk=1&area={area}&divid={divid or 'div4'}",
        )

    if not area or not divid:
        return None
    return {"area": area, "divid": divid, "webid": webid, "tree_url": tree or ""}


def parse_xxgk_tree_labels(tree_html: str) -> dict[str, str]:
    """Map sidebar label → infotypeId from dTree funclick(...) nodes."""
    out: dict[str, str] = {}
    for regex in (_FUNCLICK_RE, _FUNCLICK_LOOSE_RE):
        for match in regex.finditer(tree_html or ""):
            infotype_id = match.group(1)
            label = re.sub(r"\s+", "", match.group(2))
            if label and infotype_id and label not in out:
                out[label] = infotype_id
    return out


def resolve_xxgk_infotype(
    tree_labels: dict[str, str],
    *,
    want: str,
) -> str | None:
    """Pick infotypeId for 人事任免 / 领导简介."""
    if want == "appointment":
        for key in ("人事任免", "干部任免"):
            if key in tree_labels:
                return tree_labels[key]
        for label, iid in tree_labels.items():
            if "任免" in label:
                return iid
    if want == "leader":
        for key in ("领导简介", "领导介绍", "现任领导"):
            if key in tree_labels:
                return tree_labels[key]
        for label, iid in tree_labels.items():
            if "领导" in label:
                return iid
    return None


def fetch_xxgk_search_html(
    session,
    *,
    page_url: str,
    area: str,
    divid: str,
    webid: str,
    infotype_id: str,
    sortfield: str = "createdatetime:0,orderid:0",
) -> str | None:
    """POST module/xxgk/search.jsp the way tree.jsp funclick does."""
    ajax_url = urljoin(
        page_url,
        (
            f"/module/xxgk/search.jsp?divid={divid}&infotypeId={infotype_id}"
            f"&jdid={webid}&area={area}&sortfield={sortfield}"
        ),
    )
    response = session.post(
        ajax_url,
        data="",
        headers={
            "Referer": page_url,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
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
    if not html:
        return None
    if len(BeautifulSoup(html, "html.parser").select("a[href]")) < 1 and "任免" not in html:
        return None
    return html


def fetch_xxgk_list_html(session, page_url: str, page_html: str) -> str | None:
    """POST the xxgk search.jsp endpoint the same way loadDynamic / funclick does."""
    parsed = urlparse(page_url)
    qs = parse_qs(parsed.query)
    number = (qs.get("number") or [None])[0]
    if number is not None:
        number = number.strip() or None

    shell = extract_xxgk_shell(page_html, page_url)
    if shell:
        infotype_id = number
        if not infotype_id and shell.get("tree_url"):
            try:
                tree_resp = session.get(
                    shell["tree_url"],
                    headers={"Referer": page_url},
                    timeout=30,
                    verify=False,
                )
                tree_html = tree_resp.content.decode("utf-8", errors="ignore")
                labels = parse_xxgk_tree_labels(tree_html)
                infotype_id = resolve_xxgk_infotype(labels, want="appointment")
            except Exception:  # noqa: BLE001
                infotype_id = None
        if infotype_id:
            html = fetch_xxgk_search_html(
                session,
                page_url=page_url,
                area=shell["area"],
                divid=shell["divid"],
                webid=shell["webid"],
                infotype_id=infotype_id,
            )
            if html:
                return html

    call = find_xxgk_load_call(page_html)
    if call is None:
        return None
    path_with_query, divid, cid, webid = call
    # Prefer ?number= over empty default cid from shell page.
    if number:
        cid = number
    if not cid:
        return None
    ajax_url = urljoin(page_url, path_with_query)
    parsed_ajax = urlparse(ajax_url)
    data = (
        f"infotypeId={cid}&jdid={webid}&divid={divid}"
        f"&vc_title=&vc_number=&vc_filenumber=&vc_all=&texttype=&fbtime="
        f"&{parsed_ajax.query}"
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
