"""Unified bureau site registry.

Today: Shanghai only. Later append other province catalogs here until
headquarters → province → city → district coverage is complete.
"""

from __future__ import annotations

from tax_platform.config.sites_shanghai import BureauSite, SHANGHAI_SITES

ALL_SITES: list[BureauSite] = list(SHANGHAI_SITES)


def get_site(code: str) -> BureauSite:
    for site in ALL_SITES:
        if site.code == code:
            return site
    raise KeyError(f"Unknown bureau site code: {code}")


def list_sites(level: str | None = None) -> list[BureauSite]:
    if level is None:
        return list(ALL_SITES)
    return [site for site in ALL_SITES if site.level == level]
