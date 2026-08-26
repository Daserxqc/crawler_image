"""Discover city / district tax bureau sites under a province host."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from tax_platform.config.sites import get_site
from tax_platform.crawler.http_client import create_session, ensure_trailing_slash, fetch_html

# Shanghai-style: /pdtax/xxgk/rsrm/  or /hptax/xxgk/ldjj/
_SUBSITE_PATH_RE = re.compile(
    r"/(?P<code>[a-z][a-z0-9]{1,12})/xxgk/(?:rsrm|rsxx|ldjj)(?:/|$)",
    re.I,
)
_APPT_HINT = re.compile(r"人事(?:任免|信息)|rsrm|rsxx", re.I)
_LEADER_HINT = re.compile(r"领导(?:简介|介绍)|ldjj|ldjs", re.I)
_BUREAU_NAME = re.compile(r"([\u4e00-\u9fa5]{2,12}(?:市|区|县|盟|旗)?税务局)")


@dataclass
class CitySiteCandidate:
    code: str
    name: str
    level: str
    parent_code: str
    home_url: str
    appointment_list_url: str | None = None
    leader_intro_url: str | None = None
    region: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_entry(self) -> dict:
        data = asdict(self)
        return data


def discover_city_sites_from_html(
    html: str,
    page_url: str,
    *,
    parent_code: str,
    region: str | None = None,
) -> list[CitySiteCandidate]:
    """Parse one HTML page for child bureau xxgk paths (offline-friendly)."""
    soup = BeautifulSoup(html, "html.parser")
    by_code: dict[str, CitySiteCandidate] = {}
    base_host = urlparse(page_url).netloc

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not href or href.startswith("javascript:"):
            continue
        url = urljoin(page_url, href)
        if urlparse(url).netloc and urlparse(url).netloc != base_host:
            # Same provincial host only (e.g. shanghai.chinatax.gov.cn/*).
            if not urlparse(url).netloc.endswith(".chinatax.gov.cn"):
                continue
            if urlparse(page_url).netloc and urlparse(url).netloc != urlparse(page_url).netloc:
                continue
        match = _SUBSITE_PATH_RE.search(urlparse(url).path)
        if not match:
            continue
        code = match.group("code").lower()
        if code in {"xxgk", "web", "col", "static", "images"}:
            continue
        name = _guess_name(text, code)
        level = _guess_level(name)
        cand = by_code.get(code)
        if cand is None:
            home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/{code}/"
            cand = CitySiteCandidate(
                code=code,
                name=name,
                level=level,
                parent_code=parent_code,
                home_url=ensure_trailing_slash(home),
                region=region,
            )
            by_code[code] = cand
        path = urlparse(url).path.lower()
        if _APPT_HINT.search(path) or _APPT_HINT.search(text):
            cand.appointment_list_url = _normalize_xxgk_url(url, prefer="rsrm")
        if _LEADER_HINT.search(path) or _LEADER_HINT.search(text):
            cand.leader_intro_url = _normalize_xxgk_url(url, prefer="ldjj")

    # Fill missing appointment/leader with conventional paths.
    for cand in by_code.values():
        base = cand.home_url.rstrip("/")
        if not cand.appointment_list_url:
            cand.appointment_list_url = ensure_trailing_slash(f"{base}/xxgk/rsrm/")
            cand.notes.append("appointment_url_guessed")
        if not cand.leader_intro_url:
            cand.leader_intro_url = ensure_trailing_slash(f"{base}/xxgk/ldjj/")
            cand.notes.append("leader_url_guessed")
    return sorted(by_code.values(), key=lambda c: c.code)


def discover_province_children(
    parent_code: str,
    *,
    delay: float = 0.2,
    session=None,
) -> list[CitySiteCandidate]:
    """Fetch province home + xxgk hubs and discover child bureau sites."""
    parent = get_site(parent_code)
    owns_session = session is None
    session = session or create_session()
    pages = [
        parent.home_url,
        parent.appointment_list_url,
        parent.leader_intro_url,
        urljoin(parent.home_url, "/xxgk/"),
    ]
    merged: dict[str, CitySiteCandidate] = {}
    try:
        for page in pages:
            if not page:
                continue
            if delay:
                time.sleep(delay)
            try:
                final_url, html = fetch_html(session, page, follow_meta_refresh=True)
            except Exception as exc:  # noqa: BLE001
                continue
            for cand in discover_city_sites_from_html(
                html,
                final_url,
                parent_code=parent_code,
                region=parent.region,
            ):
                prev = merged.get(cand.code)
                if prev is None:
                    merged[cand.code] = cand
                else:
                    if cand.appointment_list_url and (
                        not prev.appointment_list_url or "guessed" in ",".join(prev.notes)
                    ):
                        prev.appointment_list_url = cand.appointment_list_url
                    if cand.leader_intro_url and (
                        not prev.leader_intro_url or "guessed" in ",".join(prev.notes)
                    ):
                        prev.leader_intro_url = cand.leader_intro_url
                    if cand.name and cand.name != cand.code:
                        prev.name = cand.name
    finally:
        if owns_session and hasattr(session, "close"):
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
    # Drop the province site itself if path matched oddly.
    merged.pop(parent_code, None)
    return sorted(merged.values(), key=lambda c: c.code)


def candidates_to_entries(candidates: list[CitySiteCandidate]) -> list[dict]:
    return [c.to_entry() for c in candidates]


def _guess_name(link_text: str, code: str) -> str:
    text = (link_text or "").strip()
    m = _BUREAU_NAME.search(text)
    if m:
        return m.group(1)
    if text and len(text) <= 20 and "税务" in text:
        return text
    return code


def _guess_level(name: str) -> str:
    if any(x in name for x in ("区", "县", "旗", "盟")):
        return "district"
    if "市" in name:
        return "city"
    return "city"


def _normalize_xxgk_url(url: str, *, prefer: str) -> str:
    """Collapse detail-ish URLs to the column root when possible."""
    parsed = urlparse(url)
    match = _SUBSITE_PATH_RE.search(parsed.path)
    if not match:
        return ensure_trailing_slash(url)
    code = match.group("code")
    root = f"{parsed.scheme}://{parsed.netloc}/{code}/xxgk/{prefer}/"
    return ensure_trailing_slash(root)
