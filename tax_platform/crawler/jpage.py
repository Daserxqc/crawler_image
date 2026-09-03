"""Helpers for government-site jpage AJAX list columns (Jiangsu / Zhejiang style)."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from tax_platform.config.list_url_normalize import prefer_http_for_legacy_ssl_hosts

DATAPROXY_RE = re.compile(
    r"/module/web/jpage/dataproxy\.jsp\?[^\"'<\s]+",
    re.IGNORECASE,
)
PARAM_RE = re.compile(
    r"var\s+param_\d+\s*=\s*\{([^}]+)\}",
    re.IGNORECASE | re.DOTALL,
)
CDATA_CHUNK_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def extract_jpage_html(html: str) -> str:
    """Unwrap CDATA record payloads into plain HTML so BeautifulSoup can see anchors."""
    chunks = CDATA_CHUNK_RE.findall(html)
    if not chunks:
        return html
    return "\n".join(chunks)


def find_dataproxy_url(html: str, page_url: str) -> str | None:
    """Build first-page dataproxy.jsp URL from jpage config embedded in the column page."""
    match = PARAM_RE.search(html)
    if match:
        blob = match.group(1)
        fields = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", blob))
        fields.update(dict(re.findall(r"(\w+)\s*:\s*(\d+)", blob)))
        webid = fields.get("webid")
        columnid = fields.get("columnid")
        unitid = fields.get("unitid")
        path = fields.get("path") or "/"
        if webid and columnid and unitid:
            query_parts = {
                "page": "1",
                "webid": webid,
                "path": path,
                "columnid": columnid,
                "unitid": unitid,
                "webname": fields.get("webname") or "",
                "permissiontype": fields.get("permissiontype") or "0",
            }
            if fields.get("appid"):
                query_parts["appid"] = fields["appid"]
            query = urlencode(query_parts)
            parsed = urlparse(page_url)
            scheme = parsed.scheme
            if (parsed.hostname or "").lower() == "jilin.chinatax.gov.cn":
                scheme = "http"
            return urlunparse((scheme, parsed.netloc, "/module/web/jpage/dataproxy.jsp", "", query, ""))

    for href in DATAPROXY_RE.findall(html):
        return urljoin(page_url, href)
    return None


def fetch_dataproxy_html(session, proxy_url: str, *, referer: str | None = None) -> str:
    """Fetch jpage dataproxy content; some sites only return records on POST."""
    proxy_url = prefer_http_for_legacy_ssl_hosts(proxy_url)
    referer = prefer_http_for_legacy_ssl_hosts(referer) if referer else referer
    headers = {}
    if referer:
        headers["Referer"] = referer
    parsed = urlparse(proxy_url)
    data = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    response = session.post(proxy_url, data=data, headers=headers, timeout=20, verify=False)
    if response.status_code < 400 and len(response.content) > 200:
        return _decode(response)
    response = session.get(proxy_url, headers=headers, timeout=20, verify=False)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code} for dataproxy {proxy_url}")
    return _decode(response)


def fetch_dataproxy_pages(
    session,
    proxy_url: str,
    *,
    referer: str | None = None,
    max_pages: int = 40,
) -> str:
    """Fetch dataproxy page=1..N and concatenate record HTML."""
    from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

    parsed = urlparse(proxy_url)
    base_q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    chunks: list[str] = []
    empty_streak = 0
    for page in range(1, max_pages + 1):
        q = dict(base_q)
        q["page"] = str(page)
        page_url = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", urlencode(q), "")
        )
        html = fetch_dataproxy_html(session, page_url, referer=referer)
        material = materialize_list_html(html)
        # Stop when a page yields no appointment-looking anchors.
        from bs4 import BeautifulSoup

        anchors = BeautifulSoup(material, "html.parser").select("a[href]")
        if len(anchors) < 1:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue
        empty_streak = 0
        chunks.append(material)
        # Typical page size ~15; if short, likely last page.
        if len(anchors) < 8 and page > 1:
            break
    return "\n".join(chunks) if chunks else ""


def _decode(response) -> str:
    encodings = [response.apparent_encoding, "utf-8", "gb18030"]
    if response.encoding and response.encoding.lower() not in {"iso-8859-1", "latin-1"}:
        encodings.insert(0, response.encoding)
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return response.content.decode(encoding, errors="ignore")
        except LookupError:
            continue
    return response.content.decode("utf-8", errors="ignore")


def materialize_list_html(html: str) -> str:
    """Return HTML that includes list anchors (unwrap CDATA when present)."""
    unwrapped = extract_jpage_html(html)
    if unwrapped != html:
        return unwrapped
    soup = BeautifulSoup(html, "html.parser")
    if soup.select("a[href]"):
        return html
    return unwrapped
