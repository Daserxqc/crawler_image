"""Canonical appointment-notice URLs (dedupe http/https and xxgkhide)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_notice_source_url(url: str | None) -> str:
    """https + drop ``xxgkhide`` query noise used by Liaoning xxgk shells."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    netloc = parts.netloc.lower()
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() != "xxgkhide"
    ]
    return urlunsplit((scheme, netloc, parts.path or "", urlencode(query), ""))


def notice_source_url_variants(url: str | None) -> list[str]:
    """Lookup variants that may already exist in the DB before normalization."""
    canon = normalize_notice_source_url(url)
    if not canon:
        return []
    parts = urlsplit(canon)
    http = urlunsplit(("http", parts.netloc, parts.path, parts.query, ""))
    https = urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
    out: list[str] = []
    for base in (canon, https, http, url or ""):
        base = (base or "").strip()
        if not base:
            continue
        for candidate in (base, f"{base}?xxgkhide=1", f"{base}&xxgkhide=1"):
            if candidate not in out:
                out.append(candidate)
    return out
