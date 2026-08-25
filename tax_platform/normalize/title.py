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
