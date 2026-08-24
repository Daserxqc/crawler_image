"""Shared helpers for crawl jobs (site selection + JSON dump)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tax_platform.config.sites_shanghai import SHANGHAI_SITES, get_site
from tax_platform.models.entities import to_dict


def resolve_site_codes(site: str) -> list[str]:
    if site == "all":
        return [item.code for item in SHANGHAI_SITES]
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
