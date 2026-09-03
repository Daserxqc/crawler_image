"""Public change feed and post incumbent/history archives (PR9)."""

from __future__ import annotations

import re
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
from tax_platform.search.display import bureau_codes_for_category, infer_unit_category
from tax_platform.store.schema import connect
from tax_platform.store.posts import (
    post_archive_from_db,
    post_department_key,
    posts_ready,
    _title_allows_multiple_holders,
)


def list_changes(
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    unit_category: str | None = None,
    change_type: str | None = None,
    department: str | None = None,
    name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """全站变动动态流（按时间倒序）。

    Default / SQL-filterable queries use COUNT + LIMIT (no full-table Python scan).
    Filters that need per-row classification (change_type / HQ category) stream once
    and only enrich the current page.
    """
    owns = conn is None
    db = conn or connect()
    cat_q = (unit_category or "").strip()
    level_q = (org_level or "").strip() or None
    dept_q = clean_department_name(department) or (department or "").strip()
    type_q = (change_type or "").strip()
    hq_cat = cat_q in {"internal", "direct", "dispatched"}
    offset = max(0, int(offset or 0))
    limit = int(limit or 0)

    where = ["1=1"]
    args: list[Any] = []
    if bureau_code:
        where.append("bureau_code = ?")
        args.append(bureau_code)
    else:
        codes = bureau_codes_for_category(org_level=level_q, unit_category=cat_q or None)
        if codes is None and level_q:
            from tax_platform.config.sites import list_sites

            codes = [s.code for s in list_sites(level=level_q)]
        if codes is not None:
            if not codes:
                if owns:
                    db.close()
                return {"total": 0, "offset": offset, "limit": limit, "items": []}
            placeholders = ",".join("?" * len(codes))
            where.append(f"bureau_code IN ({placeholders})")
            args.extend(codes)
    if name:
        where.append("person_name LIKE ?")
        args.append(f"%{name.strip()}%")
    if date_from:
        where.append("effective_on IS NOT NULL AND substr(effective_on,1,10) >= ?")
        args.append(date_from[:10])
    if date_to:
        where.append("effective_on IS NOT NULL AND substr(effective_on,1,10) <= ?")
        args.append(date_to[:10])
    if dept_q:
        where.append("(IFNULL(department_raw,'') LIKE ? OR IFNULL(title_raw,'') LIKE ?)")
        like = f"%{dept_q}%"
        args.extend([like, like])
    # Cheap SQL prefilter for change types that map cleanly to stored action/text.
    if type_q == "dismiss":
        where.append("action = 'dismiss'")
    elif type_q == "retire":
        where.append(
            "(IFNULL(raw_clause,'') LIKE '%退休%' OR IFNULL(raw_clause,'') LIKE '%离休%' "
            "OR IFNULL(notice_title,'') LIKE '%退休%')"
        )
    elif type_q in {"appoint", "promote", "transfer", "probation_confirm"}:
        where.append("action = 'appoint'")

    where_sql = " AND ".join(where)
    select_cols = (
        "id, bureau_code, person_name, action, bureau_name, department_raw, "
        "title_raw, effective_on, notice_title, source_url, raw_clause"
    )
    order_sql = "ORDER BY COALESCE(effective_on, '') DESC, id DESC"
    need_scan = bool(type_q or hq_cat)

    if not need_scan:
        total = int(
            db.execute(
                f"SELECT COUNT(*) FROM appointment_events WHERE {where_sql}",
                args,
            ).fetchone()[0]
        )
        page_limit = limit if limit > 0 else min(total, 500)
        rows = db.execute(
            f"""
            SELECT {select_cols}
            FROM appointment_events
            WHERE {where_sql}
            {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*args, page_limit, offset],
        ).fetchall()
        items = [_enrich_change_row(row, type_q=type_q) for row in rows]
        items = [i for i in items if i is not None]
        if owns:
            db.close()
        return {"total": total, "offset": offset, "limit": limit, "items": items}

    # One streaming pass: count matches, enrich only the requested page.
    matched = 0
    page: list[dict[str, Any]] = []
    end = offset + limit if limit > 0 else None
    for row in db.execute(
        f"SELECT {select_cols} FROM appointment_events WHERE {where_sql} {order_sql}",
        args,
    ):
        if not _change_row_matches(row, type_q=type_q, hq_cat=cat_q if hq_cat else None):
            continue
        if limit <= 0 or (offset <= matched and (end is None or matched < end)):
            item = _enrich_change_row(row)
            if item is not None:
                page.append(item)
        matched += 1
    if owns:
        db.close()
    return {"total": matched, "offset": offset, "limit": limit, "items": page}


def _change_row_matches(
    row: sqlite3.Row,
    *,
    type_q: str = "",
    hq_cat: str | None = None,
) -> bool:
    if not is_plausible_person_name(row["person_name"]):
        return False
    if hq_cat:
        row_cat = infer_unit_category(
            row["bureau_code"],
            {
                "department": row["department_raw"],
                "title": row["title_raw"],
                "unit": row["bureau_name"],
            },
        )
        if row_cat != hq_cat:
            return False
    if not type_q:
        return True
    ctype = classify_change(
        action=row["action"],
        title_raw=row["title_raw"],
        department_raw=row["department_raw"],
        notice_title=row["notice_title"],
        raw_clause=row["raw_clause"],
    )
    return ctype.value == type_q


def _enrich_change_row(
    row: sqlite3.Row,
    *,
    type_q: str = "",
    hq_cat: str | None = None,
) -> dict[str, Any] | None:
    if type_q or hq_cat:
        if not _change_row_matches(row, type_q=type_q, hq_cat=hq_cat):
            return None
    elif not is_plausible_person_name(row["person_name"]):
        return None
    try:
        site = get_site(row["bureau_code"])
        level = site.level
        region = site.region
    except KeyError:
        level, region = "unknown", None
    ctype = classify_change(
        action=row["action"],
        title_raw=row["title_raw"],
        department_raw=row["department_raw"],
        notice_title=row["notice_title"],
        raw_clause=row["raw_clause"],
    )
    title = normalize_title(row["title_raw"])
    dept = normalize_department(
        row["department_raw"],
        org_level=org_level_for_bureau(level),
    )
    return {
        "id": row["id"],
        "person_name": row["person_name"],
        "bureau_code": row["bureau_code"],
        "org_level": level,
        "region": region,
        "unit_category": infer_unit_category(
            row["bureau_code"],
            {
                "department": row["department_raw"],
                "title": row["title_raw"],
                "unit": row["bureau_name"],
            },
        ),
        "change_type": ctype.value,
        "action": row["action"],
        "title": title.canonical if title else row["title_raw"],
        "department": dept.canonical_name if dept else row["department_raw"],
        "bureau_name": row["bureau_name"],
        "effective_on": row["effective_on"],
        "notice_title": row["notice_title"],
        "source_url": row["source_url"],
    }


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
        row_dept = post_department_key(
            bureau_code, row["department_raw"], row["bureau_name"]
        )
        if title_q and title_q not in (row["title_raw"] or ""):
            continue
        row_compact = re.sub(r"\s+", "", row_dept or "")
        dept_compact = re.sub(r"\s+", "", dept_q or "")
        if row_compact == dept_compact or dept_compact in row_compact:
            matched.append(row)
            continue
        blob = re.sub(r"\s+", "", f"{row['department_raw'] or ''}{row['title_raw'] or ''}")
        if dept_compact and dept_compact in blob:
            matched.append(row)

    if matched:
        scoped_keys = {
            post_department_key(bureau_code, r["department_raw"], r["bureau_name"])
            for r in matched
        }
        if len(scoped_keys) > 1:
            exact = [
                r
                for r in matched
                if re.sub(
                    r"\s+",
                    "",
                    post_department_key(bureau_code, r["department_raw"], r["bureau_name"]),
                )
                == re.sub(r"\s+", "", dept_q or "")
            ]
            if exact:
                matched = exact

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

        row_dept = post_department_key(
            bureau_code, row["department_raw"], row["bureau_name"]
        )
        row_title = row["title_raw"] or ""

        def _push_past(item: dict, **extra) -> None:
            past.append({**item, **extra})

        if not _title_allows_multiple_holders(row_title):
            for other_name in list(open_tenures.keys()):
                if other_name == name:
                    continue
                other = open_tenures[other_name]
                if (other.get("title") or "") != row_title:
                    continue
                if (other.get("post_dept") or "") != row_dept:
                    continue
                other = open_tenures.pop(other_name)
                _push_past(other, ended_on=row["effective_on"], end_change_type="succeeded")

        prev = open_tenures.get(name)
        if prev and prev.get("since") != row["effective_on"]:
            _push_past(prev, ended_on=row["effective_on"], end_change_type="replaced")
        open_tenures[name] = {
            "person_name": name,
            "title": row["title_raw"],
            "department": row_dept,
            "post_dept": row_dept,
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
