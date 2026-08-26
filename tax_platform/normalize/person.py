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
        # Site chrome / nav misread as leader names (esp. STA pages).
        "专题专栏",
        "主要职能",
        "内设机构",
        "发票查询",
        "总局概况",
        "所得税司",
        "教育中心",
        "新浪微博",
        "新闻发布",
        "查看更多",
        "派出机构",
        "直属单位",
        "直属机构",
        "访问统计",
        "重要活动",
        "社保",
        "信息公开",
    }
)

_BAD_PREFIX = ("命", "任", "免", "关", "将", "其", "等")
_BAD_SUFFIX = ("局", "处", "科", "所", "部", "组", "司", "职务", "通知", "决定", "中心")
# Fragments of department / org names mis-parsed as person names (e.g. 财产和行为税处).
_DEPT_NAME_MARKERS = (
    "财产",
    "行为",
    "税收",
    "税务",
    "管理",
    "稽查",
    "办公",
    "人事",
    "党委",
    "纪检",
    "退休",
    "老干",
    "分局",
    "研究所",
    "职能",
    "概况",
    "查询",
    "微博",
    "发布",
    "统计",
    "活动",
    "机构",
    "专栏",
    "更多",
)
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
    if any(marker in text for marker in _DEPT_NAME_MARKERS):
        return False
    return True
