"""Search people by title / department and return appointment history."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tax_platform.config.sites import get_site, list_sites
from tax_platform.normalize.department import (
    clean_department_name,
    normalize_department,
    org_level_for_bureau,
    split_department_raw,
)
from tax_platform.normalize.title import normalize_title, org_level_sort_rank, title_sort_rank
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.search.display import (
    enrich_hit_display,
    headquarters_org_bucket,
    headquarters_ranked_post,
)
from tax_platform.config.sta_units import unit_keywords
from tax_platform.store.ingest import get_person_profile, person_id
from tax_platform.store.schema import connect


def search_people(
    *,
    title: str | None = None,
    department: str | None = None,
    name: str | None = None,
    org_level: str | None = None,
    bureau_code: str | None = None,
    unit_code: str | None = None,
    unit_category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return people matching title / department / name, each with appointment records.

    When *department* is set, hits include:
    - ``supervisor``: leaders whose 分管列表 covers the department
    - ``appointee``: people appointed into that department

    *date_from* / *date_to* are ISO dates (YYYY-MM-DD); filter appointment rows
    and drop pure appointees with no in-range events.

    *limit* ``0`` means enrich all matches (export). Prefer ``search_people_page``
    for UI pagination so only one page is enriched.
    """
    _total, _current, items = search_people_page(
        title=title,
        department=department,
        name=name,
        org_level=org_level,
        bureau_code=bureau_code,
        unit_code=unit_code,
        unit_category=unit_category,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
        conn=conn,
    )
    return items


def search_people_page(
    *,
    title: str | None = None,
    department: str | None = None,
    name: str | None = None,
    org_level: str | None = None,
    bureau_code: str | None = None,
    unit_code: str | None = None,
    unit_category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Collect matches cheaply, return ``(total, current_count, enriched_page)``.

    ``current_count`` is how many of *all* matches are marked 现任 in ``persons``,
    not just the current page.
    """
    title_q = (title or "").strip()
    dept_q = clean_department_name(department) or (department or "").strip()
    name_q = (name or "").strip()
    region_only = not title_q and not dept_q and not name_q
    cat_q = (unit_category or "").strip()
    offset = max(0, int(offset or 0))

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

    browse_level = org_level
    if region_only and cat_q in {"internal", "direct", "dispatched"} and not browse_level:
        browse_level = "headquarters"

    # Region browse: COUNT + LIMIT/OFFSET in SQL, enrich only the current page.
    if region_only:
        allowed_bureaus: list[str] | None = None
        if browse_level or bureau_code:
            if browse_level:
                allowed_bureaus = [s.code for s in list_sites(level=browse_level)]
            else:
                allowed_bureaus = [s.code for s in list_sites()]
            if bureau_code:
                allowed_bureaus = [b for b in allowed_bureaus if b == bureau_code] or [bureau_code]

        where = "1=1"
        args: list[Any] = []
        if allowed_bureaus is not None:
            if not allowed_bureaus:
                if owns:
                    db.close()
                return 0, 0, []
            ph = ",".join("?" * len(allowed_bureaus))
            where += f" AND bureau_code IN ({ph})"
            args.extend(allowed_bureaus)

        total = int(db.execute(f"SELECT COUNT(*) FROM persons WHERE {where}", args).fetchone()[0])
        current_count = int(
            db.execute(
                f"SELECT COUNT(*) FROM persons WHERE {where} AND COALESCE(is_current, 0) = 1",
                args,
            ).fetchone()[0]
        )
        page_limit = limit if limit > 0 else min(total, 2000)
        page_sql = (
            f"SELECT bureau_code, name FROM persons WHERE {where} "
            "ORDER BY bureau_code, name LIMIT ? OFFSET ?"
        )
        for row in db.execute(page_sql, [*args, page_limit, offset]):
            remember(row["bureau_code"], row["name"], "region_browse", "person")

        results = []
        for (bureau, person), meta in hits.items():
            pid = person_id(bureau, person)
            profile = get_person_profile(pid, conn=db)
            appointments = _filter_appointments_by_date(
                _person_appointments(db, bureau, person), date_from, date_to
            )
            results.append(
                enrich_hit_display(
                    {
                        "id": pid,
                        "name": person,
                        "bureau_code": bureau,
                        "org_level": _level_of(bureau),
                        "roles": sorted(meta["roles"]),
                        "match_reasons": meta["match_reasons"],
                        "supervised_departments": [],
                        "current": profile.get("current") if profile else None,
                        "appointments": appointments,
                        "appointment_count": len(appointments),
                        "profile": profile,
                    }
                )
            )
        if cat_q:
            results = [r for r in results if r.get("unit_category") == cat_q]
        if unit_code and unit_code not in {"", "sta"}:
            results = [r for r in results if _hit_matches_unit(r, unit_code)]
        elif unit_code == "sta":
            results = [
                r
                for r in results
                if r.get("bureau_code") == "sta" or _hit_matches_unit(r, unit_code)
            ]
        if owns:
            db.close()
        return total, current_count, results

    # 0) Name-only / name-primary: pull from persons + leaders + events by name
    if name_q and not title_q and not dept_q:
        person_sql = "SELECT bureau_code, name FROM persons WHERE name LIKE ?"
        person_args: list[Any] = [f"%{name_q}%"]
        if bureau_code:
            person_sql += " AND bureau_code = ?"
            person_args.append(bureau_code)
        for row in db.execute(person_sql, person_args):
            if not _org_level_matches(row["bureau_code"], org_level):
                continue
            remember(row["bureau_code"], row["name"], "person_name", "person")

    # 1) Leaders: title + oversight departments + name
    if not region_only:
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

        # 2) Appointment events (skipped for empty/region browse)
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
            if not _org_level_matches(
                bureau,
                org_level,
                title_raw=row["title_raw"],
                department_raw=row["department_raw"],
            ):
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

    ranked_keys = _rank_hit_keys_cheap(db, hits)
    if org_level:
        ranked_keys = [key for key in ranked_keys if _org_level_matches(key[0], org_level)]

    if date_from or date_to:
        ranked_keys = _filter_keys_by_appointment_dates(
            db, ranked_keys, hits, date_from, date_to
        )

    total = len(ranked_keys)
    current_count = _count_persons_is_current(db, ranked_keys)
    need_heavy_filter = bool((unit_code and unit_code not in {""}) or cat_q)

    if limit > 0:
        if need_heavy_filter:
            # Over-fetch then filter unit/category; still cheaper than enriching everyone.
            window = ranked_keys[offset : offset + max(limit * 25, limit)]
        else:
            window = ranked_keys[offset : offset + limit]
    else:
        window = ranked_keys

    results: list[dict[str, Any]] = []
    for bureau, person in window:
        meta = hits[(bureau, person)]
        pid = person_id(bureau, person)
        profile = get_person_profile(pid, conn=db)
        appointments = _person_appointments(db, bureau, person)
        appointments = _filter_appointments_by_date(appointments, date_from, date_to)
        supervised = departments_for_leader(person, bureau_code=bureau, conn=db)
        roles = sorted(meta["roles"])
        if dept_q and not roles:
            roles = ["supervisor"] if supervised else ["appointee"]
        results.append(
            enrich_hit_display(
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
        )

    if org_level:
        results = [
            r
            for r in results
            if (r.get("org_level") or _level_of(r.get("bureau_code") or "")) == org_level
        ]

    def _sort_key(r: dict[str, Any]) -> tuple:
        current = r.get("current") or {}
        title_text = r.get("title_display") or current.get("title") or ""
        unit_key = r.get("unit_sort_key") or r.get("unit_display") or ""
        level = r.get("org_level")
        hq_bucket = headquarters_org_bucket(r) if level == "headquarters" else 0
        if level == "headquarters" and hq_bucket == 0:
            unit_key = ""
        return (
            org_level_sort_rank(level),
            hq_bucket,
            unit_key,
            title_sort_rank(title_text),
            r.get("name") or "",
            r.get("bureau_code") or "",
        )

    results.sort(key=_sort_key)
    if unit_code and unit_code not in {"", "sta"}:
        results = [r for r in results if _hit_matches_unit(r, unit_code)]
    elif unit_code == "sta":
        results = [r for r in results if r.get("bureau_code") == "sta" or _hit_matches_unit(r, unit_code)]
    if cat_q:
        results = [r for r in results if r.get("unit_category") == cat_q]
    if limit > 0:
        results = results[:limit]
    if owns:
        db.close()
    return total, current_count, results


def _count_persons_is_current(
    db: sqlite3.Connection, keys: list[tuple[str, str]]
) -> int:
    """Count matches with persons.is_current=1 (full result set, not one page)."""
    if not keys:
        return 0
    total = 0
    chunk = 400
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        placeholders = ",".join(["(?,?)"] * len(part))
        args: list[Any] = []
        for bureau, name in part:
            args.extend([bureau, name])
        row = db.execute(
            f"""
            SELECT COUNT(*) AS n FROM persons
            WHERE COALESCE(is_current, 0) = 1
              AND (bureau_code, name) IN ({placeholders})
            """,
            args,
        ).fetchone()
        total += int(row["n"] if row and "n" in row.keys() else row[0])
    return total


def _filter_keys_by_appointment_dates(
    db: sqlite3.Connection,
    keys: list[tuple[str, str]],
    hits: dict[tuple[str, str], dict[str, Any]],
    date_from: str | None,
    date_to: str | None,
) -> list[tuple[str, str]]:
    """Keep keys that have an in-range appointment, or are supervisor-only matches."""
    if not keys or (not date_from and not date_to):
        return keys
    start = (date_from or "")[:10]
    end = (date_to or "")[:10]
    in_range: set[tuple[str, str]] = set()
    chunk = 400
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        placeholders = ",".join(["(?,?)"] * len(part))
        args: list[Any] = []
        for bureau, name in part:
            args.extend([bureau, name])
        rows = db.execute(
            f"""
            SELECT bureau_code, person_name, effective_on
            FROM appointment_events
            WHERE (bureau_code, person_name) IN ({placeholders})
            """,
            args,
        ).fetchall()
        # Also pull same-name events (other bureau) for keys with no local rows.
        local_names = {name for _b, name in part}
        name_ph = ",".join("?" * len(local_names)) if local_names else ""
        by_name: dict[str, list[sqlite3.Row]] = {}
        if local_names:
            for row in db.execute(
                f"""
                SELECT bureau_code, person_name, effective_on
                FROM appointment_events
                WHERE person_name IN ({name_ph})
                """,
                list(local_names),
            ):
                by_name.setdefault(row["person_name"], []).append(row)

        seen_local: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["bureau_code"], row["person_name"])
            seen_local.add(key)
            day = (row["effective_on"] or "")[:10]
            if not day:
                if not start:
                    in_range.add(key)
                continue
            if start and day < start:
                continue
            if end and day > end:
                continue
            in_range.add(key)

        for bureau, name in part:
            if (bureau, name) in seen_local:
                continue
            for row in by_name.get(name, []):
                day = (row["effective_on"] or "")[:10]
                if not day:
                    if not start:
                        in_range.add((bureau, name))
                        break
                    continue
                if start and day < start:
                    continue
                if end and day > end:
                    continue
                in_range.add((bureau, name))
                break

    out: list[tuple[str, str]] = []
    for key in keys:
        if key in in_range:
            out.append(key)
            continue
        roles = hits.get(key, {}).get("roles") or set()
        if "supervisor" in roles:
            out.append(key)
    return out


def _rank_hit_keys_cheap(
    db: sqlite3.Connection, hits: dict[tuple[str, str], dict[str, Any]]
) -> list[tuple[str, str]]:
    """Order match keys using persons.title_current when available (no N+1 profile)."""
    if not hits:
        return []
    titles: dict[tuple[str, str], str] = {}
    keys = list(hits.keys())
    chunk = 400
    for i in range(0, len(keys), chunk):
        part = keys[i : i + chunk]
        placeholders = ",".join(["(?,?)"] * len(part))
        args: list[Any] = []
        for bureau, name in part:
            args.extend([bureau, name])
        rows = db.execute(
            f"""
            SELECT bureau_code, name, COALESCE(title_current, '') AS title_current
            FROM persons
            WHERE (bureau_code, name) IN ({placeholders})
            """,
            args,
        ).fetchall()
        for row in rows:
            titles[(row["bureau_code"], row["name"])] = row["title_current"] or ""

    def key_fn(item: tuple[str, str]) -> tuple:
        bureau, name = item
        return (
            org_level_sort_rank(_level_of(bureau)),
            title_sort_rank(titles.get(item, "")),
            name,
            bureau,
        )

    return sorted(keys, key=key_fn)


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
    department: str | None = None,
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    staff_limit: int = 30,
    staff_offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """一站式：科室 → 分管领导 + 任职人员。

    When *department* is empty, browse staff under the region filters (全体).
    """
    owns = conn is None
    db = conn or connect()
    dept_q = (clean_department_name(department) or (department or "")).strip()
    staff_offset = max(0, int(staff_offset or 0))
    page_n = staff_limit if staff_limit > 0 else 100
    supervisors: list[dict[str, Any]] = []
    if dept_q:
        supervisors = leaders_for_department(
            dept_q,
            org_level=org_level,
            bureau_code=bureau_code,
            conn=db,
        )
    total, _current, people = search_people_page(
        department=dept_q or None,
        org_level=org_level,
        bureau_code=bureau_code,
        limit=page_n,
        offset=staff_offset,
        conn=db,
    )
    if dept_q:
        staff = [p for p in people if "appointee" in (p.get("roles") or [])]
        if not staff:
            staff = people
    else:
        staff = people
    result = {
        "department": dept_q or "全部",
        "org_level": org_level,
        "bureau_code": bureau_code,
        "supervising_leaders": supervisors,
        "staff": staff,
        "supervisor_count": len(supervisors),
        "staff_count": total,
        "staff_limit": page_n,
        "staff_offset": staff_offset,
        "browse_all": not bool(dept_q),
    }
    if owns:
        db.close()
    return result


def penetrate_department(
    department: str | None = None,
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    staff_limit: int = 20,
    staff_offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """层级穿透：科室 → 分管领导 → 单位层级信息。空科室=按地区浏览全体。"""
    owns = conn is None
    db = conn or connect()
    # Browse-all needs a larger page than single-dept drill-down.
    dept_q = (department or "").strip()
    limit = staff_limit if dept_q else max(staff_limit, 100)
    base = lookup_department(
        department,
        org_level=org_level,
        bureau_code=bureau_code,
        staff_limit=limit,
        staff_offset=staff_offset,
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
    args.append(max(limit * 3, limit))
    rows = [dict(r) for r in db.execute(sql, args).fetchall()]
    # Dedupe identical canonical names (catalog may store same dept under multiple levels).
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        name = (row.get("canonical_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(row)
        if len(unique) >= limit:
            break
    if owns:
        db.close()
    return unique


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
    level_short = {
        "headquarters": "总局",
        "province": "省级",
        "city": "市局",
        "district": "区县",
    }
    title_levels: dict[str, set[str]] = {}
    for row in rows:
        title_levels.setdefault(row["canonical_title"], set()).add(row["org_level"])
    for row in rows:
        title = row["canonical_title"]
        if len(title_levels.get(title, set())) > 1:
            row["display_label"] = f"{title}（{level_short.get(row['org_level'], row['org_level'])}）"
        else:
            row["display_label"] = title
    if owns:
        db.close()
    return rows


def suggest_names(
    q: str = "",
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    limit: int = 100,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    owns = conn is None
    db = conn or connect()
    sql = "SELECT DISTINCT name, bureau_code FROM persons WHERE 1=1"
    args: list[Any] = []
    if bureau_code:
        sql += " AND bureau_code = ?"
        args.append(bureau_code)
    if q:
        sql += " AND name LIKE ?"
        args.append(f"%{q}%")
    sql += " ORDER BY name LIMIT ?"
    args.append(limit)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in db.execute(sql, args):
        if org_level and not _bureau_level_ok(row["bureau_code"], org_level):
            continue
        name = row["name"]
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name})
    if owns:
        db.close()
    return out


def _level_of(bureau_code: str) -> str:
    try:
        return get_site(bureau_code).level
    except KeyError:
        return "unknown"


def _bureau_level_ok(bureau_code: str, org_level: str) -> bool:
    return _level_of(bureau_code) == org_level


def _has_region_scope(
    org_level: str | None,
    bureau_code: str | None,
    unit_code: str | None,
    unit_category: str | None = None,
) -> bool:
    return bool(
        (org_level or "").strip()
        or (bureau_code or "").strip()
        or (unit_code or "").strip()
        or (unit_category or "").strip()
    )


def _hit_matches_unit(hit: dict[str, Any], unit_code: str) -> bool:
    keywords = unit_keywords(unit_code)
    if not keywords:
        return True
    chunks: list[str] = []
    current = hit.get("current") or {}
    chunks.append(str(current.get("department") or ""))
    chunks.append(str(current.get("title") or ""))
    chunks.append(str(current.get("unit") or ""))
    for row in hit.get("appointments") or []:
        chunks.append(str(row.get("department_raw") or ""))
        chunks.append(str(row.get("title_raw") or ""))
        chunks.append(str(row.get("bureau_name") or ""))
    text = "".join(chunks)
    return any(k in text for k in keywords)


def _org_level_matches(
    bureau_code: str,
    org_level: str | None,
    *,
    title_raw: str | None = None,
    department_raw: str | None = None,
) -> bool:
    if not org_level:
        return True
    if _bureau_level_ok(bureau_code, org_level):
        return True
    if org_level == "headquarters" and headquarters_ranked_post(title_raw, department_raw):
        return True
    return False


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
    """Load appointment rows for a person.

    Prefer same-bureau matches; if none, fall back to **all** events with the
    same person name (leaders often appear under one bureau while notices were
    ingested under another).
    """
    rows = db.execute(
        """
        SELECT bureau_code, action, title_raw, department_raw, bureau_name,
               effective_on, notice_title, source_url, raw_clause
        FROM appointment_events
        WHERE bureau_code = ? AND person_name = ?
        ORDER BY COALESCE(effective_on, '') DESC, id DESC
        """,
        (bureau_code, name),
    ).fetchall()
    if not rows:
        rows = db.execute(
            """
            SELECT bureau_code, action, title_raw, department_raw, bureau_name,
                   effective_on, notice_title, source_url, raw_clause
            FROM appointment_events
            WHERE person_name = ?
            ORDER BY COALESCE(effective_on, '') DESC, id DESC
            """,
            (name,),
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
        try:
            deps = json.loads(row["departments_json"] or "[]")
        except json.JSONDecodeError:
            deps = []
        dept_text = " ".join(str(d) for d in deps)
        if not _org_level_matches(
            row["bureau_code"],
            org_level,
            title_raw=row["title_raw"],
            department_raw=dept_text,
        ):
            continue
        if not is_plausible_person_name(row["person_name"]):
            continue
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
