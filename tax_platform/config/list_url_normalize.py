"""Normalize bureau appointment / leader list URLs before fetch."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def is_html_file_path(path: str) -> bool:
    """True for *.html / *.htm / *.shtml — must not get a trailing slash."""
    p = (path or "").rstrip("/").lower()
    return p.endswith((".html", ".htm", ".shtml"))


def normalize_list_url(url: str) -> str:
    """Fix common registry mistakes that break fetch (Hebei right.html/, etc.)."""
    u = (url or "").strip()
    if not u:
        return u
    parsed = urlparse(u)
    path = parsed.path or ""
    # Hebei / some manual entries: right.html/ → 412→404; strip trailing slash on *.html
    if path.endswith("/") and is_html_file_path(path):
        path = path.rstrip("/")
    # Jilin: HTTPS often legacy-SSL; prefer HTTP for col shells (browser still works).
    host = (parsed.hostname or "").lower()
    if host == "jilin.chinatax.gov.cn" and parsed.scheme == "https":
        return urlunparse(parsed._replace(scheme="http", path=path))
    # Shanxi son/list: HTTPS or trailing slash often 403; plain HTTP path works.
    if host == "shanxi.chinatax.gov.cn":
        path = path.rstrip("/") if "/son/list/" in path else path
        if parsed.scheme == "https":
            parsed = parsed._replace(scheme="http", path=path)
            return urlunparse(parsed)
        if path != parsed.path:
            return urlunparse(parsed._replace(path=path))
    if path != parsed.path:
        parsed = parsed._replace(path=path)
        return urlunparse(parsed)
    return u


def prefer_http_for_legacy_ssl_hosts(url: str) -> str:
    """Jilin (and similar) break on HTTPS legacy renegotiation — keep HTTP for API calls."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "jilin.chinatax.gov.cn" and parsed.scheme == "https":
        return urlunparse(parsed._replace(scheme="http"))
    return url
