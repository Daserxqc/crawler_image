"""Load / merge discovered city & district bureau sites from JSON registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tax_platform.config.bureau_site import BureauSite

DEFAULT_CITY_REGISTRY = Path("output/city_sites_registry.json")


def load_city_registry(path: Path = DEFAULT_CITY_REGISTRY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return data


def save_city_registry(entries: list[dict[str, Any]], path: Path = DEFAULT_CITY_REGISTRY) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_code = {e["code"]: e for e in entries if e.get("code")}
    merged = [by_code[c] for c in sorted(by_code)]
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def merge_city_entries(
    entries: list[dict[str, Any]],
    *,
    path: Path = DEFAULT_CITY_REGISTRY,
) -> list[dict[str, Any]]:
    by_code = {e["code"]: e for e in load_city_registry(path) if e.get("code")}
    for entry in entries:
        code = entry.get("code")
        if not code:
            continue
        by_code[code] = {**by_code.get(code, {}), **entry}
    save_city_registry(list(by_code.values()), path)
    return load_city_registry(path)


def entry_to_bureau_site(entry: dict[str, Any]) -> BureauSite | None:
    code = (entry.get("code") or "").strip()
    appt = (entry.get("appointment_list_url") or "").strip()
    leader = (entry.get("leader_intro_url") or "").strip()
    home = (entry.get("home_url") or "").strip()
    if not code:
        return None
    # Need at least one crawlable list URL.
    if not appt and not leader:
        return None
    if not home:
        seed = appt or leader
        if "/xxgk/" in seed:
            home = seed.rsplit("/xxgk/", 1)[0] + "/"
        elif "/col/col" in seed:
            home = seed
        else:
            home = seed
    if appt and "?" not in appt and not appt.endswith("/") and "index.html" not in appt:
        appt = appt + "/"
    if leader and "?" not in leader and not leader.endswith("/") and "index.html" not in leader:
        leader = leader + "/"
    if not leader and appt:
        leader = appt
        for old, new in (("/xxgk/rsrm/", "/xxgk/ldjj/"), ("/xxgk/rsxx/", "/xxgk/ldjj/")):
            if old in appt:
                leader = appt.replace(old, new)
                break
    if not appt:
        # Leader-only city hub (e.g. some Shandong cities); appointments crawl will skip.
        appt = ""
    level = entry.get("level") or "city"
    if level not in {"city", "district"}:
        level = "city"
    return BureauSite(
        code=code,
        name=entry.get("name") or code,
        level=level,
        parent_code=entry.get("parent_code"),
        home_url=home,
        appointment_list_url=appt,
        leader_intro_url=leader or appt,
        region=entry.get("region"),
    )


def load_registry_sites(path: Path = DEFAULT_CITY_REGISTRY) -> list[BureauSite]:
    sites: list[BureauSite] = []
    for entry in load_city_registry(path):
        site = entry_to_bureau_site(entry)
        if site:
            sites.append(site)
    return sites
