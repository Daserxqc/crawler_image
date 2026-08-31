"""Shared watch ↔ appointment_event matching (API + email notify)."""

from __future__ import annotations

import sqlite3
from typing import Any


def parse_watch_target(target_type: str, target_id: str) -> dict[str, str | None]:
    target_type = (target_type or "").strip()
    target_id = (target_id or "").strip()
    if target_type == "bureau":
        return {"bureau_code": target_id, "department": None, "title": None, "person_name": None}
    if target_type == "person":
        bureau_code = None
        person_name = target_id
        if ":" in target_id:
            bureau_code, person_name = target_id.split(":", 1)
        return {
            "bureau_code": bureau_code or None,
            "department": None,
            "title": None,
            "person_name": person_name,
        }
    if target_type in {"department", "post"}:
        parts = target_id.split("::", 2)
        bureau_code = parts[0] if parts else None
        department = parts[1] if len(parts) > 1 else None
        title = parts[2] if len(parts) > 2 and target_type == "post" else None
        return {
            "bureau_code": bureau_code,
            "department": department,
            "title": title,
            "person_name": None,
        }
    return {"bureau_code": None, "department": None, "title": None, "person_name": None}


def event_matches_watch(event: sqlite3.Row | dict[str, Any], watch: sqlite3.Row | dict[str, Any]) -> bool:
    wt = watch["target_type"] if isinstance(watch, dict) else watch["target_type"]
    wid = watch["target_id"] if isinstance(watch, dict) else watch["target_id"]
    parsed = parse_watch_target(wt, wid)

    bureau = event["bureau_code"] if isinstance(event, dict) else event["bureau_code"]
    person = event["person_name"] if isinstance(event, dict) else event["person_name"]
    dept = (event.get("department_raw") if isinstance(event, dict) else event["department_raw"]) or ""
    dept = dept or (event.get("department") if isinstance(event, dict) else "") or ""
    title = (event.get("title_raw") if isinstance(event, dict) else event["title_raw"]) or ""
    title = title or (event.get("title") if isinstance(event, dict) else "") or ""

    if wt == "bureau":
        return bureau == parsed["bureau_code"]
    if wt == "person":
        if parsed["person_name"] != person:
            return False
        if parsed["bureau_code"]:
            return bureau == parsed["bureau_code"]
        return True
    if wt == "department":
        if bureau != parsed["bureau_code"]:
            return False
        dept_q = (parsed["department"] or "").strip()
        return bool(dept_q and dept_q in dept)
    if wt == "post":
        if bureau != parsed["bureau_code"]:
            return False
        dept_q = (parsed["department"] or "").strip()
        if dept_q and dept_q not in dept:
            return False
        title_q = (parsed["title"] or "").strip()
        if title_q and title_q not in title:
            return False
        return bool(dept_q or title_q)
    return False


def build_department_target_id(bureau_code: str, department: str) -> str:
    return f"{bureau_code.strip()}::{department.strip()}"


def build_post_target_id(bureau_code: str, department: str, title: str = "") -> str:
    parts = [bureau_code.strip(), department.strip()]
    if title.strip():
        parts.append(title.strip())
    return "::".join(parts)
