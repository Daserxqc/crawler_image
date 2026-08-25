"""Infer ChangeType from appointment rows / notice text."""

from __future__ import annotations

import re

from tax_platform.models.entities import ChangeType

_RETIRE_RE = re.compile(r"退休|离休|到龄")
_PROBATION_RE = re.compile(r"试用期(?:已?满|期满)?.*转正|转正任职|期满转正")
_TRANSFER_HINT_RE = re.compile(r"调任|交流|挂职|兼任")
_PROMOTE_HINT_RE = re.compile(r"提任|晋升|提拔")

# Rough title rank for promote detection (higher index = higher rank).
_TITLE_RANK = (
    "科员",
    "副科长",
    "科长",
    "副所长",
    "所长",
    "副处长",
    "处长",
    "副主任",
    "主任",
    "副局长",
    "局长",
    "总会计师",
    "总经济师",
    "总审计师",
    "一级巡视员",
    "二级巡视员",
    "党委书记",
)


def classify_change(
    *,
    action: str | None,
    title_raw: str | None = None,
    department_raw: str | None = None,
    notice_title: str | None = None,
    raw_clause: str | None = None,
    previous_title: str | None = None,
    previous_department: str | None = None,
) -> ChangeType:
    """Map a stored event (+ optional previous post) to ChangeType."""
    blob = "".join(
        part or ""
        for part in (notice_title, raw_clause, title_raw, department_raw)
    )
    action_l = (action or "").strip().lower()

    if _RETIRE_RE.search(blob):
        return ChangeType.RETIRE
    if _PROBATION_RE.search(blob):
        return ChangeType.PROBATION_CONFIRM
    if action_l == "dismiss" or "免去" in blob[:20]:
        return ChangeType.DISMISS
    if _TRANSFER_HINT_RE.search(blob):
        return ChangeType.TRANSFER
    if _PROMOTE_HINT_RE.search(blob):
        return ChangeType.PROMOTE

    if action_l == "appoint" and previous_title:
        if _rank(title_raw) > _rank(previous_title):
            return ChangeType.PROMOTE
        prev_dept = (previous_department or "").strip()
        cur_dept = (department_raw or "").strip()
        if prev_dept and cur_dept and prev_dept != cur_dept:
            return ChangeType.TRANSFER
        if previous_title != (title_raw or "") and cur_dept and prev_dept == cur_dept:
            return ChangeType.PROMOTE

    if action_l == "appoint":
        return ChangeType.APPOINT
    if action_l == "dismiss":
        return ChangeType.DISMISS
    return ChangeType.UNKNOWN


def _rank(title: str | None) -> int:
    if not title:
        return -1
    text = title.strip()
    best = -1
    for i, token in enumerate(_TITLE_RANK):
        if token in text:
            best = max(best, i)
    return best
