"""Gather appointment list URL candidates from stored sources (no blind guessing).

Priority (first hit wins for *reference*; all collected for validation):
  1. city_sites_registry.json
  2. org_units table (tax_hr.db)
  3. province overrides (sites_provinces.py)
  4. known ingest-script fallbacks (manual ingest metadata)
  5. Henan city path pattern from ingest_henan_manual (henan_path_* only)

If nothing is found, return empty and caller should ask the user.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tax_platform.config.city_sites_io import DEFAULT_CITY_REGISTRY, load_city_registry
from tax_platform.config.list_url_normalize import normalize_list_url
from tax_platform.config.sites_provinces import PROVINCE_URL_OVERRIDES
from tax_platform.paths import default_db_path

# Curated fallbacks from manual ingest scripts — not guessed from the web.
INGEST_SCRIPT_FALLBACKS: dict[str, str] = {
    "henan": "https://henan.chinatax.gov.cn/xxgk/rsgl/rsrm/",
    "guangdong_shenzhen": (
        "https://shenzhen.chinatax.gov.cn/sztax/zdgkml/zsjs/rsjy/gkmlrsrm/zfxxgk_zdgk_list.shtml"
    ),
    "shandong_qingdao": "https://qingdao.chinatax.gov.cn/xxgk2019/rsxx/rsrm/",
    "fujian_fj_xmsswj": "https://xiamen.chinatax.gov.cn/xxgk/rsrm/",
    "liaoning_dalian": "https://dalian.chinatax.gov.cn/col/col12234/index.html",
    "zhejiang_ningbo": "https://ningbo.chinatax.gov.cn/col/col12234/index.html",
}

HENAN_CITY_APPT_PATH = "/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/"


@dataclass
class UrlCandidate:
    url: str
    source: str  # registry | org_units | province_override | ingest_script | henan_pattern


@dataclass
class RediscoveryResult:
    bureau_code: str
    candidates: list[UrlCandidate] = field(default_factory=list)
    validated_url: str | None = None
    validated_items: int = 0
    needs_user: bool = False
    notes: list[str] = field(default_factory=list)


def _registry_entry(code: str, registry: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    rows = registry if registry is not None else load_city_registry()
    for row in rows:
        if row.get("code") == code:
            return row
    return None


def _org_units_url(code: str, db_path: Path | str) -> str | None:
    path = Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT appointment_list_url FROM org_units WHERE code = ?",
            (code,),
        ).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    finally:
        conn.close()
    return None


def _henan_city_pattern_url(code: str) -> str | None:
    if not code.startswith("henan_path_"):
        return None
    slug = code.removeprefix("henan_path_")
    return f"https://henan.chinatax.gov.cn/{slug}{HENAN_CITY_APPT_PATH}"


def collect_url_candidates(
    code: str,
    *,
    db_path: Path | str | None = None,
    registry: list[dict[str, Any]] | None = None,
) -> list[UrlCandidate]:
    """Return deduped candidate URLs from stored sources only."""
    db_path = Path(db_path) if db_path is not None else default_db_path()
    seen: set[str] = set()
    out: list[UrlCandidate] = []

    def add(url: str | None, source: str) -> None:
        if not url:
            return
        u = normalize_list_url(url.strip())
        if not u or u in seen:
            return
        seen.add(u)
        out.append(UrlCandidate(url=u, source=source))

    row = _registry_entry(code, registry)
    if row:
        add(row.get("appointment_list_url"), "registry")

    add(_org_units_url(code, db_path), "org_units")

    prov = PROVINCE_URL_OVERRIDES.get(code, {})
    add(prov.get("appointment_list_url"), "province_override")

    add(INGEST_SCRIPT_FALLBACKS.get(code), "ingest_script")

    add(_henan_city_pattern_url(code), "henan_pattern")

    return out


def validate_list_url(url: str, *, timeout: int = 20) -> tuple[bool, int, str]:
    """Quick requests check: status OK and parser finds list items."""
    import requests

    from tax_platform.crawler.appointment_list import parse_appointment_list

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/127.0.0.0"}
    try:
        resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc)[:200]
    if resp.status_code >= 400:
        return False, 0, f"HTTP {resp.status_code}"
    html = resp.content.decode(resp.apparent_encoding or "utf-8", errors="ignore")
    items = parse_appointment_list(html, resp.url)
    if items:
        return True, len(items), resp.url
    if "任免" in html and len(html) > 2000:
        return True, 0, resp.url
    return False, 0, f"parsed 0 items (html={len(html)})"


def rediscover_bureau_url(
    code: str,
    *,
    db_path: Path | str | None = None,
    registry_path: Path = DEFAULT_CITY_REGISTRY,
) -> RediscoveryResult:
    db_path = Path(db_path) if db_path is not None else default_db_path()
    registry = load_city_registry(registry_path)
    result = RediscoveryResult(bureau_code=code)
    result.candidates = collect_url_candidates(code, db_path=db_path, registry=registry)

    if not result.candidates:
        result.needs_user = True
        result.notes.append("no stored URL candidate — ask user for screenshot / link")
        return result

    for cand in result.candidates:
        ok, n_items, detail = validate_list_url(cand.url)
        if ok:
            result.validated_url = cand.url if detail.startswith("http") else cand.url
            # prefer final redirect URL when parser used it
            if detail.startswith("http"):
                result.validated_url = normalize_list_url(detail)
            result.validated_items = n_items
            result.notes.append(f"validated via {cand.source}: {n_items} items")
            return result
        result.notes.append(f"reject {cand.source} {cand.url[:80]}: {detail}")

    result.needs_user = True
    result.notes.append("all stored candidates failed validation — ask user")
    return result
