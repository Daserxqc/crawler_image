"""Shared helpers for crawl jobs (site selection + JSON dump)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tax_platform.config.sites import ALL_SITES, BureauSite, get_site, list_sites
from tax_platform.crawler.crawl_state import (
    load_crawl_state,
    sites_due_for_crawl,
)
from tax_platform.models.entities import to_dict


def resolve_site_codes(
    site: str,
    *,
    kind: str | None = None,
    due_only: bool = False,
    level: str | None = None,
) -> list[str]:
    if due_only:
        if kind is None:
            raise ValueError("kind is required when due_only=True")
        records = load_crawl_state()
        due_sites = sites_due_for_crawl(kind, records=records, level=level)
        if site == "all":
            return [item.code for item in due_sites]
        get_site(site)
        return [site] if any(item.code == site for item in due_sites) else []

    if site == "all":
        sites = list_sites(level) if level else ALL_SITES
        return [item.code for item in sites]
    get_site(site)
    return [site]


def dump_json(path: str | Path, payload: Any) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out.resolve()


def serialize_crawl_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "__dataclass_fields__"):
        data = {}
        for key in result.__dataclass_fields__:
            value = getattr(result, key)
            if isinstance(value, list) and value and hasattr(value[0], "__dataclass_fields__"):
                data[key] = [to_dict(item) for item in value]
            else:
                data[key] = value
        return data
    raise TypeError(f"Unsupported crawl result: {type(result)!r}")


def site_or_raise(code: str) -> BureauSite:
    return get_site(code)
