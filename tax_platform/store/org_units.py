"""Organizational unit master data (tax bureau sites) in SQLite."""

from __future__ import annotations

import sqlite3
from typing import Any

from tax_platform.config.sites import ALL_SITES, reload_sites


ORG_UNITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS org_units (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    level TEXT NOT NULL,
    parent_code TEXT,
    region TEXT,
    home_url TEXT,
    appointment_list_url TEXT,
    leader_intro_url TEXT,
    sort_name TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_org_units_level ON org_units(level, region);
CREATE INDEX IF NOT EXISTS idx_org_units_parent ON org_units(parent_code);
"""


def ensure_org_units_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ORG_UNITS_SCHEMA)


def sync_org_units_from_sites(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Upsert org_units from the in-code site registry.

    Returns number of rows written. Skips work when table already has rows unless *force*.
    """
    ensure_org_units_schema(conn)
    if not force:
        n = conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0]
        if n > 0:
            return 0

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sites = reload_sites() if force else ALL_SITES
    count = 0
    for site in sites:
        sort_name = (site.name or "").replace("国家税务总局", "").replace("税务局", "").strip()
        conn.execute(
            """
            INSERT INTO org_units (
                code, name, level, parent_code, region,
                home_url, appointment_list_url, leader_intro_url, sort_name, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                level=excluded.level,
                parent_code=excluded.parent_code,
                region=excluded.region,
                home_url=excluded.home_url,
                appointment_list_url=excluded.appointment_list_url,
                leader_intro_url=excluded.leader_intro_url,
                sort_name=excluded.sort_name,
                updated_at=excluded.updated_at
            """,
            (
                site.code,
                site.name,
                site.level,
                site.parent_code,
                site.region,
                site.home_url,
                site.appointment_list_url,
                site.leader_intro_url,
                sort_name or site.code,
                now,
            ),
        )
        count += 1
    return count


def list_org_units(
    conn: sqlite3.Connection,
    *,
    level: str | None = None,
    parent_code: str | None = None,
    region: str | None = None,
) -> list[dict[str, Any]]:
    ensure_org_units_schema(conn)
    sql = (
        "SELECT code, name, level, parent_code, region, home_url, "
        "appointment_list_url, leader_intro_url, sort_name "
        "FROM org_units WHERE 1=1"
    )
    args: list[Any] = []
    if level:
        sql += " AND level = ?"
        args.append(level)
    if parent_code:
        sql += " AND parent_code = ?"
        args.append(parent_code)
    if region:
        sql += " AND region = ?"
        args.append(region)
    sql += " ORDER BY level, sort_name, code"
    return [dict(row) for row in conn.execute(sql, args).fetchall()]


def get_org_unit(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    ensure_org_units_schema(conn)
    row = conn.execute(
        """
        SELECT code, name, level, parent_code, region, home_url,
               appointment_list_url, leader_intro_url, sort_name
        FROM org_units WHERE code = ?
        """,
        (code,),
    ).fetchone()
    return dict(row) if row else None
