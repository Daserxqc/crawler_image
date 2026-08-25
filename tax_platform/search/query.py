"""Search people by title / department and return appointment history."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tax_platform.config.sites import get_site
from tax_platform.normalize.department import (
    clean_department_name,
    normalize_department,
    org_level_for_bureau,
    split_department_raw,
)
from tax_platform.normalize.title import normalize_title
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import get_person_profile, person_id
from tax_platform.store.schema import connect


def search_people(
    *,
    title: str | None = None,
    department: str | None = None,
    name: str | None = None,
    org_level: str | None = None,
    bureau_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return people matching title / department / name, each with appointment records.

    When *department* is set, hits include:
    - ``supervisor``: leaders whose 分管列表 covers the department
    - ``appointee``: people appointed into that department

    *date_from* / *date_to* are ISO dates (YYYY-MM-DD); filter appointment rows
    and drop pure appointees with no in-range events.
    """
    title_q = (title or "").strip()
    dept_q = clean_department_name(department) or (department or "").strip()
    name_q = (name or "").strip()
    if not title_q and not dept_q and not name_q:
        raise ValueError("Provide at least one of title / department / name")

    owns = conn is None
    db = conn or connect()

    hits: dict[tuple[str, str], dict[str, Any]] = {}

    def remember(
        bureau: str,
        person: str,
        reason: str,
        source: str,
        *,
        role: str | None = None,
    ) -> None:
        if not is_plausible_person_name(person):
            return
        if name_q and name_q not in person:
            return
        key = (bureau, person)
        item = hits.setdefault(
            key,
            {
                "bureau_code": bureau,
                "person_name": person,
                "match_reasons": [],
                "sources": set(),
                "roles": set(),
            },
        )
        if reason not in item["match_reasons"]:
            item["match_reasons"].append(reason)
        item["sources"].add(source)
        if role:
            item["roles"].add(role)

    # 0) Name-only / name-primary: pull from persons + leaders + events by name
    if name_q and not title_q and not dept_q:
        person_sql = "SELECT bureau_code, name FROM persons WHERE name LIKE ?"
        person_args: list[Any] = [f"%{name_q}%"]
        if bureau_code:
            person_sql += " AND bureau_code = ?"
            person_args.append(bureau_code)
        for row in db.execute(person_sql, person_args):
            if org_level and not _bureau_level_ok(row["bureau_code"], org_level):
                continue
            remember(row["bureau_code"], row["name"], "person_name", "person")

    # 1) Leaders: title + oversight departments + name
    for row in _iter_leaders(db, bureau_code=bureau_code, org_level=org_level):
        bureau = row["bureau_code"]
        person = row["person_name"]
        deps = row["departments"]
        if name_q and name_q not in person:
            continue
        if title_q and _title_matches(row["title_raw"], title_q):
            remember(bureau, person, f"leader_title:{row['title_raw']}", "leader")
        if name_q and not title_q and not dept_q:
            remember(bureau, person, "leader_name", "leader")
        if dept_q:
            matched_dep = None
            for raw in deps:
                if _dept_matches(str(raw), dept_q, org_level=_level_of(bureau)):
                    matched_dep = raw
                    break
            if matched_dep is not None:
                remember(
                    bureau,
                    person,
                    f"supervisor:{matched_dep}",
                    "leader",
                    role="supervisor",
                )
            elif _dept_matches(row["title_raw"] or "", dept_q, org_level=_level_of(bureau)):
                remember(
                    bureau,
                    person,
                    f"leader_title_dept:{row['title_raw']}",
                    "leader",
                    role="appointee",
                )

    # 2) Appointment events
    event_sql = (
        "SELECT bureau_code, person_name, title_raw, department_raw, action, "
        "effective_on, notice_title, source_url, raw_clause "
        "FROM appointment_events WHERE 1=1"
    )
    event_args: list[Any] = []
    if bureau_code:
        event_sql += " AND bureau_code = ?"
        event_args.append(bureau_code)
    if name_q:
        event_sql += " AND person_name LIKE ?"
        event_args.append(f"%{name_q}%")
    for row in db.execute(event_sql, event_args):
        bureau = row["bureau_code"]
        if org_level and not _bureau_level_ok(bureau, org_level):
            continue
        person = row["person_name"]
        matched = False
        if name_q and not title_q and not dept_q:
            remember(bureau, person, "event_name", "event")
            matched = True
        if title_q and _title_matches(row["title_raw"], title_q):
            remember(bureau, person, f"event_title:{row['title_raw']}", "event")
            matched = True
        if dept_q and _dept_matches(
            row["department_raw"] or "",
            dept_q,
            org_level=_level_of(bureau),
        ):
            remember(
                bureau,
                person,
                f"event_dept:{row['department_raw']}",
                "event",
                role="appointee",
            )
            matched = True
        if dept_q and _dept_matches(row["title_raw"] or "", dept_q, org_level=_level_of(bureau)):
            remember(
                bureau,
                person,
                f"event_title_dept:{row['title_raw']}",
                "event",
                role="appointee",
            )
            matched = True
        if not matched:
            continue

    results: list[dict[str, Any]] = []
    for (bureau, person), meta in hits.items():
        pid = person_id(bureau, person)
        profile = get_person_profile(pid, conn=db)
        appointments = _person_appointments(db, bureau, person)
        appointments = _filter_appointments_by_date(appointments, date_from, date_to)
        supervised = departments_for_leader(person, bureau_code=bureau, conn=db)
        roles = sorted(meta["roles"])
        if dept_q and not roles:
            roles = (
                ["supervisor"]
                if supervised
                else ["appointee"]
            )
        # Date filter: drop pure appointees with no remaining events
        if (date_from or date_to) and not appointments and "supervisor" not in roles:
            if "appointee" in roles or not dept_q:
                # name/title hits without in-range events also drop
                if "supervisor" not in roles:
                    continue
        results.append(
            {
                "id": pid,
                "name": person,
                "bureau_code": bureau,
                "org_level": _level_of(bureau),
                "roles": roles,
                "match_reasons": meta["match_reasons"],
                "supervised_departments": supervised[0]["departments"] if supervised else [],
                "current": profile.get("current") if profile else None,
                "appointments": appointments,
                "appointment_count": len(appointments),
                "profile": profile,
            }
        )

    def _sort_key(r: dict[str, Any]) -> tuple:
        is_sup = 0 if "supervisor" in r.get("roles", []) else 1
        return (is_sup, -r["appointment_count"], r["bureau_code"], r["name"])

    results.sort(key=_sort_key)
    if limit > 0:
        results = results[:limit]
    if owns:
        db.close()
    return results


def leaders_for_department(
    department: str,
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    limit: int = 50,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """科室 → 分管领导（来自领导介绍 departments_json）。"""
    dept_q = clean_department_name(department) or (department or "").strip()
    if not dept_q:
        raise ValueError("department is required")

    owns = conn is None
    db = conn or connect()
    out: list[dict[str, Any]] = []
    for row in _iter_leaders(db, bureau_code=bureau_code, org_level=org_level):
        matched = [
            d
            for d in row["departments"]
            if _dept_matches(str(d), dept_q, org_level=_level_of(row["bureau_code"]))
        ]
        if not matched:
            continue
        out.append(
            {
                "id": person_id(row["bureau_code"], row["person_name"]),
                "name": row["person_name"],
                "bureau_code": row["bureau_code"],
                "org_level": _level_of(row["bureau_code"]),
                "title_raw": row["title_raw"],
                "duty_summary": row["duty_summary"],
                "matched_departments": matched,
                "departments": row["departments"],
                "source_url": row["source_url"],
                "role": "supervisor",
            }
        )
    out.sort(key=lambda r: (r["bureau_code"], r["name"]))
    if limit > 0:
        out = out[:limit]
    if owns:
        db.close()
    return out


def departments_for_leader(
    name: str,
    *,
    bureau_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """领导 → 分管科室列表。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")

    owns = conn is None
    db = conn or connect()
    sql = (
        "SELECT bureau_code, person_name, title_raw, duty_summary, "
        "departments_json, source_url FROM leader_duties WHERE person_name = ?"
    )
    args: list[Any] = [name]
    if bureau_code:
        sql += " AND bureau_code = ?"
        args.append(bureau_code)
    sql += " ORDER BY id DESC"
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in db.execute(sql, args):
        key = (row["bureau_code"], row["person_name"])
        if key in seen:
            continue
        seen.add(key)
        try:
            deps = json.loads(row["departments_json"] or "[]")
        except json.JSONDecodeError:
            deps = []
        out.append(
            {
                "id": person_id(row["bureau_code"], row["person_name"]),
                "name": row["person_name"],
                "bureau_code": row["bureau_code"],
                "org_level": _level_of(row["bureau_code"]),
                "title_raw": row["title_raw"],
                "duty_summary": row["duty_summary"],
                "departments": [str(d) for d in deps],
                "source_url": row["source_url"],
            }
        )
    if owns:
        db.close()
    return out


def lookup_department(
    department: str,
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    staff_limit: int = 30,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """一站式：科室 → 分管领导 + 任职人员。"""
    owns = conn is None
    db = conn or connect()
    supervisors = leaders_for_department(
        department,
        org_level=org_level,
        bureau_code=bureau_code,
        conn=db,
    )
    people = search_people(
        department=department,
        org_level=org_level,
        bureau_code=bureau_code,
        limit=0,
        conn=db,
    )
    staff = [p for p in people if "appointee" in p.get("roles", [])]
    if staff_limit > 0:
        staff = staff[:staff_limit]
    result = {
        "department": clean_department_name(department) or department,
        "org_level": org_level,
        "bureau_code": bureau_code,
        "supervising_leaders": supervisors,
        "staff": staff,
        "supervisor_count": len(supervisors),
        "staff_count": len(staff),
    }
    if owns:
        db.close()
    return result


def penetrate_department(
    department: str,
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """层级穿透：科室 → 分管领导 → 单位层级信息。"""
    owns = conn is None
    db = conn or connect()
    base = lookup_department(
        department,
        org_level=org_level,
        bureau_code=bureau_code,
        staff_limit=20,
        conn=db,
    )
    upward: list[dict[str, Any]] = []
    for lead in base["supervising_leaders"]:
        try:
            site = get_site(lead["bureau_code"])
            region = site.region
            parent = site.parent_code
        except KeyError:
            region = None
            parent = None
        upward.append(
            {
                "department": base["department"],
                "matched_departments": lead.get("matched_departments"),
                "leader": {
                    "id": lead["id"],
                    "name": lead["name"],
                    "title_raw": lead.get("title_raw"),
                    "source_url": lead.get("source_url"),
                },
                "bureau_code": lead["bureau_code"],
                "org_level": lead["org_level"],
                "region": region,
                "parent_bureau_code": parent,
            }
        )
    result = {
        **base,
        "upward": upward,
    }
    if owns:
        db.close()
    return result


def suggest_departments(
    query: str = "",
    *,
    org_level: str | None = None,
    limit: int = 30,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Suggest departments from dept_catalog for cascading UI filters."""
    owns = conn is None
    db = conn or connect()
    q = clean_department_name(query) or query.strip()
    sql = "SELECT canonical_name, kind, org_level, source_count FROM dept_catalog WHERE 1=1"
    args: list[Any] = []
    if org_level:
        sql += " AND org_level = ?"
        args.append(org_level)
    if q:
        sql += " AND canonical_name LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY source_count DESC, canonical_name LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in db.execute(sql, args).fetchall()]
    if owns:
        db.close()
    return rows


def suggest_titles(
    query: str = "",
    *,
    org_level: str | None = None,
    limit: int = 30,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    owns = conn is None
    db = conn or connect()
    q = (query or "").strip()
    sql = "SELECT canonical_title, org_level, source_count FROM title_catalog WHERE 1=1"
    args: list[Any] = []
    if org_level:
        sql += " AND org_level = ?"
        args.append(org_level)
    if q:
        sql += " AND canonical_title LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY source_count DESC, canonical_title LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in db.execute(sql, args).fetchall()]
    if owns:
        db.close()
    return rows


def _level_of(bureau_code: str) -> str:
    try:
        return get_site(bureau_code).level
    except KeyError:
        return "unknown"


def _bureau_level_ok(bureau_code: str, org_level: str) -> bool:
    return _level_of(bureau_code) == org_level


def _title_matches(raw: str | None, query: str) -> bool:
    if not raw:
        return False
    if query in raw:
        return True
    nt = normalize_title(raw)
    return bool(nt and query in nt.canonical)


def _dept_matches(raw: str, query: str, *, org_level: str | None = None) -> bool:
    if not raw:
        return False
    if query in raw:
        return True
    level = org_level_for_bureau(org_level or "district")
    for part in split_department_raw(raw):
        if query in part:
            return True
        nd = normalize_department(part, org_level=level)
        if nd and (query in nd.canonical_name or nd.canonical_name in query):
            return True
    nd = normalize_department(raw, org_level=level)
    return bool(nd and query in nd.canonical_name)


def _person_appointments(
    db: sqlite3.Connection, bureau_code: str, name: str
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT action, title_raw, department_raw, bureau_name, effective_on,
               notice_title, source_url, raw_clause
        FROM appointment_events
        WHERE bureau_code = ? AND person_name = ?
        ORDER BY COALESCE(effective_on, '') DESC, id DESC
        """,
        (bureau_code, name),
    ).fetchall()
    return [dict(row) for row in rows]


def _filter_appointments_by_date(
    appointments: list[dict[str, Any]],
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    if not date_from and not date_to:
        return appointments
    start = (date_from or "")[:10]
    end = (date_to or "")[:10]
    out: list[dict[str, Any]] = []
    for row in appointments:
        day = (row.get("effective_on") or "")[:10]
        if not day:
            # Keep undated rows only when no lower bound (otherwise they never match range).
            if not start:
                out.append(row)
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        out.append(row)
    return out


def _iter_leaders(
    db: sqlite3.Connection,
    *,
    bureau_code: str | None = None,
    org_level: str | None = None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT bureau_code, person_name, title_raw, duty_summary, "
        "departments_json, source_url FROM leader_duties WHERE 1=1"
    )
    args: list[Any] = []
    if bureau_code:
        sql += " AND bureau_code = ?"
        args.append(bureau_code)
    rows: list[dict[str, Any]] = []
    for row in db.execute(sql, args):
        if org_level and not _bureau_level_ok(row["bureau_code"], org_level):
            continue
        if not is_plausible_person_name(row["person_name"]):
            continue
        try:
            deps = json.loads(row["departments_json"] or "[]")
        except json.JSONDecodeError:
            deps = []
        rows.append(
            {
                "bureau_code": row["bureau_code"],
                "person_name": row["person_name"],
                "title_raw": row["title_raw"],
                "duty_summary": row["duty_summary"],
                "departments": [str(d) for d in deps],
                "source_url": row["source_url"],
            }
        )
    return rows
