from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BureauSite:
    """One tax bureau data source (any hierarchy level)."""

    code: str
    name: str
    # headquarters | province | city | district
    level: str
    parent_code: str | None
    home_url: str
    appointment_list_url: str
    leader_intro_url: str
    refresh_days: int | None = None
    region: str | None = None  # e.g. 上海市 / 广东省
