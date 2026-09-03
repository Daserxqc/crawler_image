"""Crawl refresh intervals by bureau hierarchy.

All levels default to a weekly refresh; Site.refresh_days can still override.
"""

from __future__ import annotations

# Default cadence (days). Site.refresh_days can override.
DEFAULT_REFRESH_DAYS: dict[str, int] = {
    "headquarters": 7,
    "province": 7,
    "city": 7,
    "district": 7,
}


def refresh_days_for(level: str, override: int | None = None) -> int:
    if override is not None:
        return override
    if level not in DEFAULT_REFRESH_DAYS:
        raise KeyError(f"Unknown bureau level for schedule: {level!r}")
    return DEFAULT_REFRESH_DAYS[level]
