from __future__ import annotations

import re

from tax_platform.models.entities import NormalizedTitle

RANK_HINT_RE = re.compile(r"（([^）]+级)）|\(([^)]+级)\)")
HOSTING_RE = re.compile(r"主持[^，。；;]*工作")
TITLE_SUFFIXES = (
    "一级巡视员",
    "二级巡视员",
    "巡视员",
    "纪检组组长",
    "总会计师",
    "总经济师",
    "总审计师",
    "副局长",
    "局长",
    "副司长",
    "司长",
    "副处长",
    "处长",
    "副主任",
    "主任",
    "副所长",
    "所长",
    "副科长",
    "科长",
    "党委副书记",
    "党委书记",
    "党委委员",
    "副组长",
    "组长",
    "专员",
)

# Lower number = higher in listing (总局局长 first).
_TITLE_SORT_RANK: tuple[tuple[str, int], ...] = (
    ("党委书记", 10),
    ("局长", 20),
    ("常务副局长", 30),
    ("纪检组组长", 40),
    ("总法律顾问", 45),
    ("总会计师", 46),
    ("总经济师", 47),
    ("总审计师", 48),
    ("副局长", 50),
    ("党委副书记", 55),
    ("党委委员", 60),
    ("司长", 65),
    ("副司长", 68),
    ("一级巡视员", 70),
    ("二级巡视员", 75),
    ("巡视员", 80),
    ("处长", 90),
    ("主任", 95),
    ("一级高级主办", 100),
    ("二级高级主办", 105),
    ("三级高级主办", 110),
    ("四级高级主办", 115),
    ("副处长", 120),
    ("副主任", 125),
    ("调研员", 130),
    ("科长", 140),
    ("所长", 145),
    ("一级主办", 150),
    ("二级主办", 155),
    ("三级主办", 160),
    ("四级主办", 165),
    ("副科长", 170),
    ("副所长", 175),
    ("组长", 180),
    ("副组长", 185),
    ("主办", 190),
    ("专员", 200),
)


def normalize_title(raw: str | None) -> NormalizedTitle | None:
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw)
    rank_match = RANK_HINT_RE.search(text)
    rank_hint = (rank_match.group(1) or rank_match.group(2)) if rank_match else None
    text = RANK_HINT_RE.sub("", text)
    text = HOSTING_RE.sub("", text)
    canonical = text.strip("、，,；; ")
    for suffix in TITLE_SUFFIXES:
        if canonical.endswith(suffix):
            return NormalizedTitle(raw=raw, canonical=canonical, rank_hint=rank_hint)
    return NormalizedTitle(raw=raw, canonical=canonical or raw, rank_hint=rank_hint)


def title_sort_rank(raw: str | None) -> int:
    """Return sort rank for a title string (lower = higher leadership)."""
    if not raw:
        return 900
    text = re.sub(r"\s+", "", str(raw))
    text = RANK_HINT_RE.sub("", text)
    text = HOSTING_RE.sub("", text)
    # Prefer the first concurrent title segment (e.g. 副局长、二级高级主办).
    primary = text.split("、")[0].strip("，,；; ") or text
    # Longest suffix first so 「副局长」does not collapse to 「局长」.
    for suffix, rank in sorted(_TITLE_SORT_RANK, key=lambda x: -len(x[0])):
        if primary.endswith(suffix):
            return rank
    return 800


def org_level_sort_rank(level: str | None) -> int:
    """总局 < 省局 < 市局 < 区县."""
    order = {
        "headquarters": 0,
        "province": 1,
        "city": 2,
        "district": 3,
    }
    return order.get(level or "", 9)
