"""Current-tenure helpers from appointment timelines."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tax_platform.normalize.change import classify_change
from tax_platform.normalize.department import normalize_department, org_level_for_bureau
from tax_platform.normalize.title import normalize_title
from tax_platform.config.sites import get_site

_APPOINT_TYPES = frozenset({"appoint", "transfer", "promote", "probation_confirm"})
_LEAVE_TYPES = frozenset({"dismiss", "retire"})


def build_current_from_history(
    history: list[dict[str, Any]],
    *,
    leader: dict[str, Any] | None = None,
    person_title: str | None = None,
) -> dict[str, Any]:
    """Derive current post from reverse-chrono history (+ optional leader intro)."""
    # Walk newest → oldest. Latest appoint wins only if no newer leave closes that post.
    open_post: dict[str, Any] | None = None
    newer_leaves: list[dict[str, Any]] = []
    for row in history:
        ctype = row.get("change_type") or ""
        if ctype in _LEAVE_TYPES:
            newer_leaves.append(row)
            continue
        if ctype in _APPOINT_TYPES:
            if any(_leave_closes(leave, row) for leave in newer_leaves):
                continue
            open_post = row
            break

    departments: list[str] = []
    title = person_title
    if leader:
        if leader.get("title_raw"):
            title = leader["title_raw"]
        raw_deps = leader.get("departments_json") or leader.get("departments") or []
        if isinstance(raw_deps, str):
            try:
                departments = json.loads(raw_deps)
            except json.JSONDecodeError:
                departments = []
        else:
            departments = list(raw_deps)

    if open_post:
        nt = normalize_title(open_post.get("title") or title)
        return {
            "unit": open_post.get("unit"),
            "department": open_post.get("department") or (departments[0] if departments else None),
            "title": nt.canonical if nt else (open_post.get("title") or title),
            "departments": departments,
            "is_current": True,
            "since": open_post.get("date"),
            "source_url": open_post.get("source_url"),
        }

    # Appointment history says the person left — do not force 现任 from a stale leader page.
    left = bool(newer_leaves) or _newest_is_leave(history)

    if leader:
        nt = normalize_title(title)
        return {
            "unit": None,
            "department": departments[0] if departments else None,
            "title": nt.canonical if nt else title,
            "departments": departments,
            "is_current": not left,
            "since": None,
            "source_url": leader.get("source_url"),
        }

    return {
        "unit": None,
        "department": None,
        "title": normalize_title(person_title).canonical if normalize_title(person_title) else person_title,
        "departments": [],
        "is_current": False,
        "since": None,
        "source_url": None,
    }


def enrich_history_rows(
    events: list[sqlite3.Row] | list[dict[str, Any]],
    *,
    bureau_code: str,
) -> list[dict[str, Any]]:
    """Convert appointment event rows to history dicts with ChangeType."""
    try:
        level = get_site(bureau_code).level
        level_label = _level_label(level)
    except KeyError:
        level = "district"
        level_label = None

    # oldest → newest for previous-post comparison, then reverse for display
    materialized: list[dict[str, Any]] = []
    ordered = list(events)
    # assume input already newest-first from SQL; reverse for prev tracking
    chronological = list(reversed(ordered))
    prev_title = None
    prev_dept = None
    for row in chronological:
        data = dict(row) if not isinstance(row, dict) else row
        change = classify_change(
            action=data.get("action"),
            title_raw=data.get("title_raw"),
            department_raw=data.get("department_raw"),
            notice_title=data.get("notice_title"),
            raw_clause=data.get("raw_clause"),
            previous_title=prev_title,
            previous_department=prev_dept,
        )
        title = normalize_title(data.get("title_raw"))
        dept = normalize_department(
            data.get("department_raw"),
            org_level=org_level_for_bureau(level),
        )
        item = {
            "date": data.get("effective_on"),
            "level": level_label,
            "unit": data.get("bureau_name"),
            "department": dept.canonical_name if dept else data.get("department_raw"),
            "title": title.canonical if title else data.get("title_raw"),
            "change_type": change.value,
            "notice_title": data.get("notice_title"),
            "source_url": data.get("source_url"),
            "raw_clause": data.get("raw_clause"),
            "action": data.get("action"),
        }
        materialized.append(item)
        if change.value not in _LEAVE_TYPES:
            prev_title = data.get("title_raw") or prev_title
            prev_dept = data.get("department_raw") or prev_dept

    materialized.reverse()  # newest first
    return materialized


def _leave_closes(leave: dict[str, Any], appoint: dict[str, Any]) -> bool:
    """True if a newer leave event closes the older appoint."""
    if _same_post(leave, appoint):
        return True
    # Blanket leave (免去职务) with empty title/dept closes any prior open post.
    leave_title = (leave.get("title") or "").strip()
    leave_dept = (leave.get("department") or "").strip()
    if not leave_title and not leave_dept:
        return True
    # Title-only leave (免去处长职务) closes matching title regardless of dept.
    if leave_title and not leave_dept:
        return leave_title == (appoint.get("title") or "").strip()
    return False


def _newest_is_leave(history: list[dict[str, Any]]) -> bool:
    for row in history:
        ctype = row.get("change_type") or ""
        if ctype in _LEAVE_TYPES:
            return True
        if ctype in _APPOINT_TYPES:
            return False
    return False


def _same_post(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a.get("title") or "") == (b.get("title") or "") and (
        a.get("department") or ""
    ) == (b.get("department") or "")


def _level_label(level: str | None) -> str | None:
    mapping = {
        "headquarters": "总局层面",
        "province": "省局层面",
        "city": "市局层面",
        "district": "区局层面",
    }
    return mapping.get(level or "")
