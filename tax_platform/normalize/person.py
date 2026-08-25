"""Person-name plausibility checks shared by parsers, search, and cleanup."""

from __future__ import annotations

import re

# Common false positives from appointment list/nav text and glued 「任命Name为」.
SKIP_NAMES = frozenset(
    {
        "人事",
        "决定",
        "任命",
        "免去",
        "经研究",
        "各单位",
        "现将",
        "现予",
        "公告",
        "通知",
        "有关",
        "省税务局",
        "市税务局",
        "区税务局",
        "县税务局",
        "税务局",
        "国家税务",
        "工作人员",
    }
)

_BAD_PREFIX = ("命", "任", "免", "关", "将", "其", "等")
_BAD_SUFFIX = ("局", "处", "科", "所", "部", "组", "职务", "通知", "决定")
_NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")


def is_plausible_person_name(name: str | None) -> bool:
    """Return True if *name* looks like a real Chinese person name."""
    if not name:
        return False
    text = name.strip()
    if text in SKIP_NAMES:
        return False
    if not _NAME_RE.fullmatch(text):
        return False
    if "税务" in text:
        return False
    if text.startswith(_BAD_PREFIX):
        return False
    if text.endswith(_BAD_SUFFIX):
        return False
    return True
