"""Unified bureau site registry: headquarters → provinces → cities/districts."""

from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite
from tax_platform.config.city_sites_io import load_registry_sites
from tax_platform.config.sites_headquarters import STA_HEADQUARTERS
from tax_platform.config.sites_provinces import PROVINCE_SITES
from tax_platform.config.sites_shanghai import SHANGHAI_SITES


def _build_all_sites() -> list[BureauSite]:
    base: list[BureauSite] = [
        STA_HEADQUARTERS,
        *PROVINCE_SITES,
        *SHANGHAI_SITES,
    ]
    seen = {site.code for site in base}
    extra: list[BureauSite] = []
    for site in load_registry_sites():
        if site.code in seen:
            continue
        extra.append(site)
        seen.add(site.code)
    return [*base, *extra]


ALL_SITES: list[BureauSite] = _build_all_sites()


def reload_sites() -> list[BureauSite]:
    """Reload ALL_SITES after the city registry file changes (tests / CLI)."""
    global ALL_SITES
    ALL_SITES = _build_all_sites()
    return ALL_SITES


def get_site(code: str) -> BureauSite:
    for site in ALL_SITES:
        if site.code == code:
            return site
    raise KeyError(f"Unknown bureau site code: {code}")


def list_sites(level: str | None = None, region: str | None = None) -> list[BureauSite]:
    sites = ALL_SITES
    if level is not None:
        sites = [site for site in sites if site.level == level]
    if region is not None:
        sites = [site for site in sites if site.region == region]
    return list(sites)
