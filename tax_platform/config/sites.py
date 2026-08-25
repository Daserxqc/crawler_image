"""Unified bureau site registry: headquarters → provinces → cities/districts."""

from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite
from tax_platform.config.sites_headquarters import STA_HEADQUARTERS
from tax_platform.config.sites_provinces import PROVINCE_SITES
from tax_platform.config.sites_shanghai import SHANGHAI_SITES

ALL_SITES: list[BureauSite] = [
    STA_HEADQUARTERS,
    *PROVINCE_SITES,
    *SHANGHAI_SITES,
]


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
