"""Build hierarchy–department / title catalogs from stored crawl data."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from typing import Any

from tax_platform.config.sites import get_site
from tax_platform.normalize.department import (
    department_from_title,
    normalize_department,
    org_level_for_bureau,
    split_department_raw,
)
from tax_platform.normalize.title import normalize_title
from tax_platform.store.schema import connect


def rebuild_catalogs(*, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Mine dept/title catalogs from appointment_events + leader_duties."""
    owns = conn is None
    db = conn or connect()

    dept_stats: dict[tuple[str, str], dict[str, Any]] = {}
    title_stats: dict[tuple[str, str], int] = defaultdict(int)

    def add_dept(name: str, org_level: str, bureau_code: str) -> None:
        normalized = normalize_department(name, org_level=org_level_for_bureau(org_level))
        if normalized is None:
            return
        # Catalog only keeps names that look like real org units.
        cn = normalized.canonical_name
        if not any(
            cn.endswith(s)
            for s in ("处", "科", "司", "所", "办公室", "办公厅", "中心", "分局", "局")
        ):
            return
        if len(cn) > 20:
            return
        key = (cn, org_level)
        bucket = dept_stats.setdefault(
            key,
            {
                "kind": normalized.kind.value,
                "count": 0,
                "bureaus": set(),
            },
        )
        bucket["count"] += 1
        bucket["bureaus"].add(bureau_code)

    def add_title(raw: str | None, org_level: str) -> None:
        nt = normalize_title(raw)
        if nt is None or not nt.canonical:
            return
        if len(nt.canonical) > 60:
            return
        title_stats[(nt.canonical, org_level)] += 1

    for row in db.execute(
        "SELECT bureau_code, department_raw, title_raw FROM appointment_events"
    ):
        bureau = row["bureau_code"]
        try:
            level = get_site(bureau).level
        except KeyError:
            level = "province" if bureau != "sta" else "headquarters"
        for part in split_department_raw(row["department_raw"]):
            add_dept(part, level, bureau)
        embedded = department_from_title(row["title_raw"])
        if embedded:
            add_dept(embedded, level, bureau)
        add_title(row["title_raw"], level)

    for row in db.execute(
        "SELECT bureau_code, title_raw, departments_json FROM leader_duties"
    ):
        bureau = row["bureau_code"]
        try:
            level = get_site(bureau).level
        except KeyError:
            level = "province"
        add_title(row["title_raw"], level)
        embedded = department_from_title(row["title_raw"])
        if embedded:
            add_dept(embedded, level, bureau)
        try:
            deps = json.loads(row["departments_json"] or "[]")
        except json.JSONDecodeError:
            deps = []
        for raw in deps:
            for part in split_department_raw(str(raw)):
                add_dept(part, level, bureau)

    db.execute("DELETE FROM dept_catalog")
    db.execute("DELETE FROM title_catalog")
    for (name, level), meta in dept_stats.items():
        db.execute(
            """
            INSERT INTO dept_catalog (canonical_name, kind, org_level, source_count, sample_bureaus)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                meta["kind"],
                level,
                meta["count"],
                ",".join(sorted(meta["bureaus"])[:12]),
            ),
        )
    for (title, level), count in title_stats.items():
        db.execute(
            """
            INSERT INTO title_catalog (canonical_title, org_level, source_count)
            VALUES (?, ?, ?)
            """,
            (title, level, count),
        )
    db.commit()
    result = {
        "dept_rows": len(dept_stats),
        "title_rows": len(title_stats),
    }
    if owns:
        db.close()
    return result


def list_departments_for_level(
    org_level: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    owns = conn is None
    db = conn or connect()
    rows = db.execute(
        """
        SELECT canonical_name, kind, org_level, source_count, sample_bureaus
        FROM dept_catalog
        WHERE org_level = ?
        ORDER BY source_count DESC, canonical_name
        """,
        (org_level,),
    ).fetchall()
    result = [dict(row) for row in rows]
    if owns:
        db.close()
    return result


def department_level_matrix(
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Show which org levels each department name appears at."""
    owns = conn is None
    db = conn or connect()
    rows = db.execute(
        """
        SELECT canonical_name,
               GROUP_CONCAT(org_level) AS levels,
               SUM(source_count) AS total_count
        FROM dept_catalog
        GROUP BY canonical_name
        ORDER BY total_count DESC, canonical_name
        """
    ).fetchall()
    result = []
    for row in rows:
        levels = sorted(set((row["levels"] or "").split(",")))
        result.append(
            {
                "canonical_name": row["canonical_name"],
                "levels": levels,
                "total_count": row["total_count"],
                "province_only": levels == ["province"],
                "district_only": levels == ["district"],
                "cross_level": len(levels) > 1,
            }
        )
    if owns:
        db.close()
    return result
