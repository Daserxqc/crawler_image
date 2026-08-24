"""Crawl refresh intervals by bureau hierarchy.

Higher levels change less often, so they are crawled less often.
"""

from __future__ import annotations

# Default cadence (days). Site.refresh_days can override.
DEFAULT_REFRESH_DAYS: dict[str, int] = {
    "headquarters": 90,  # 总局：约三个月
    "province": 30,  # 省局 / 直辖市局：约一个月
    "city": 14,  # 地市局：约两周
    "district": 7,  # 区县局：约一周
}


def refresh_days_for(level: str, override: int | None = None) -> int:
    if override is not None:
        return override
    if level not in DEFAULT_REFRESH_DAYS:
        raise KeyError(f"Unknown bureau level for schedule: {level!r}")
    return DEFAULT_REFRESH_DAYS[level]
