"""Public change feed and post incumbent/history archives (PR9)."""

from __future__ import annotations

import sqlite3
from typing import Any

from tax_platform.config.sites import get_site
from tax_platform.normalize.change import classify_change
from tax_platform.normalize.department import (
    clean_department_name,
    normalize_department,
    org_level_for_bureau,
)
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.normalize.title import normalize_title
from tax_platform.store.schema import connect


def list_changes(
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    change_type: str | None = None,
    department: str | None = None,
    name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """全站变动动态流（按时间倒序）。"""
    owns = conn is None
    db = conn or connect()

    sql = (
        "SELECT id, bureau_code, person_name, action, bureau_name, department_raw, "
        "title_raw, effective_on, notice_title, source_url, raw_clause "
        "FROM appointment_events WHERE 1=1"
    )
    args: list[Any] = []
    if bureau_code:
        sql += " AND bureau_code = ?"
        args.append(bureau_code)
    if name:
        sql += " AND person_name LIKE ?"
        args.append(f"%{name.strip()}%")
    if date_from:
        sql += " AND effective_on IS NOT NULL AND substr(effective_on,1,10) >= ?"
        args.append(date_from[:10])
    if date_to:
        sql += " AND effective_on IS NOT NULL AND substr(effective_on,1,10) <= ?"
        args.append(date_to[:10])
    sql += " ORDER BY COALESCE(effective_on, '') DESC, id DESC"

    dept_q = clean_department_name(department) or (department or "").strip()
    type_q = (change_type or "").strip()
    items: list[dict[str, Any]] = []
    for row in db.execute(sql, args):
        if not is_plausible_person_name(row["person_name"]):
            continue
        try:
            site = get_site(row["bureau_code"])
            level = site.level
            region = site.region
        except KeyError:
            level, region = "unknown", None
        if org_level and level != org_level:
            continue
        if dept_q:
            blob = f"{row['department_raw'] or ''}{row['title_raw'] or ''}"
            if dept_q not in blob:
                continue
        ctype = classify_change(
            action=row["action"],
            title_raw=row["title_raw"],
            department_raw=row["department_raw"],
            notice_title=row["notice_title"],
            raw_clause=row["raw_clause"],
        )
        if type_q and ctype.value != type_q:
            continue
        title = normalize_title(row["title_raw"])
        dept = normalize_department(
            row["department_raw"],
            org_level=org_level_for_bureau(level),
        )
        items.append(
            {
                "id": row["id"],
                "person_name": row["person_name"],
                "bureau_code": row["bureau_code"],
                "org_level": level,
                "region": region,
                "change_type": ctype.value,
                "action": row["action"],
                "title": title.canonical if title else row["title_raw"],
                "department": dept.canonical_name if dept else row["department_raw"],
                "bureau_name": row["bureau_name"],
                "effective_on": row["effective_on"],
                "notice_title": row["notice_title"],
                "source_url": row["source_url"],
            }
        )

    total = len(items)
    page = items[offset : offset + limit] if limit > 0 else items[offset:]
    if owns:
        db.close()
    return {"total": total, "offset": offset, "limit": limit, "items": page}


def post_archive(
    *,
    bureau_code: str,
    department: str,
    title: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """岗位现任 / 历任：按单位 + 科室（+ 可选职务）聚合。"""
    dept_q = clean_department_name(department) or department.strip()
    title_q = (title or "").strip()
    if not bureau_code or not dept_q:
        raise ValueError("bureau_code and department are required")

    owns = conn is None
    db = conn or connect()

    from tax_platform.store.posts import post_archive_from_db, posts_ready

    if posts_ready(db):
        cached = post_archive_from_db(
            bureau_code=bureau_code,
            department=department,
            title=title,
            conn=db,
        )
        if cached is not None:
            if owns:
                db.close()
            return cached

    rows = db.execute(
        """
        SELECT * FROM appointment_events
        WHERE bureau_code = ?
        ORDER BY COALESCE(effective_on, '') ASC, id ASC
        """,
        (bureau_code,),
    ).fetchall()

    matched: list[sqlite3.Row] = []
    for row in rows:
        if not is_plausible_person_name(row["person_name"]):
            continue
        blob = f"{row['department_raw'] or ''}{row['title_raw'] or ''}"
        if dept_q not in blob:
            continue
        if title_q and title_q not in (row["title_raw"] or ""):
            continue
        matched.append(row)

    # Track each person's latest open tenure on this post.
    open_tenures: dict[str, dict[str, Any]] = {}
    past: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    seen_event_keys: set[tuple[str, str, str, str, str, str]] = set()

    for row in matched:
        name = row["person_name"]
        ctype = classify_change(
            action=row["action"],
            title_raw=row["title_raw"],
            department_raw=row["department_raw"],
            notice_title=row["notice_title"],
            raw_clause=row["raw_clause"],
            previous_title=(open_tenures.get(name) or {}).get("title"),
            previous_department=(open_tenures.get(name) or {}).get("department"),
        )
        event_key = (
            name,
            row["action"],
            row["department_raw"] or "",
            row["title_raw"] or "",
            row["effective_on"] or "",
            ctype.value,
        )
        if event_key in seen_event_keys:
            continue
        seen_event_keys.add(event_key)

        event = {
            "person_name": name,
            "change_type": ctype.value,
            "title": row["title_raw"],
            "department": row["department_raw"],
            "effective_on": row["effective_on"],
            "notice_title": row["notice_title"],
            "source_url": row["source_url"],
        }
        history.append(event)

        if ctype.value in {"dismiss", "retire"}:
            prev = open_tenures.pop(name, None)
            if prev:
                past.append({**prev, "ended_on": row["effective_on"], "end_change_type": ctype.value})
            continue

        # Successor appoint: close other open incumbents on the *same* title/post.
        row_title = row["title_raw"] or ""
        for other_name in list(open_tenures.keys()):
            if other_name == name:
                continue
            other = open_tenures[other_name]
            if (other.get("title") or "") != row_title:
                continue
            other = open_tenures.pop(other_name)
            past.append({**other, "ended_on": row["effective_on"], "end_change_type": "succeeded"})

        prev = open_tenures.get(name)
        if prev and prev.get("since") != row["effective_on"]:
            past.append({**prev, "ended_on": row["effective_on"], "end_change_type": "replaced"})
        open_tenures[name] = {
            "person_name": name,
            "title": row["title_raw"],
            "department": row["department_raw"],
            "since": row["effective_on"],
            "source_url": row["source_url"],
            "change_type": ctype.value,
        }

    incumbents = list(open_tenures.values())
    history.reverse()  # newest first for display

    try:
        site = get_site(bureau_code)
        level, region = site.level, site.region
    except KeyError:
        level, region = "unknown", None

    result = {
        "bureau_code": bureau_code,
        "org_level": level,
        "region": region,
        "department": dept_q,
        "title": title_q or None,
        "incumbents": incumbents,
        "past": list(reversed(past)),
        "history": history,
        "incumbent_count": len(incumbents),
        "history_count": len(history),
        "source": "live",
    }
    if owns:
        db.close()
    return result
