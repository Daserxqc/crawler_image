"""Stable person identities (separates 'who' from 'bureau+name appearance')."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

def legacy_person_id(bureau_code: str, name: str) -> str:
    return f"{bureau_code}:{name}"


IDENTITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS person_identities (
    id TEXT PRIMARY KEY,
    primary_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS person_name_keys (
    bureau_code TEXT NOT NULL,
    name TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    PRIMARY KEY (bureau_code, name),
    FOREIGN KEY (identity_id) REFERENCES person_identities(id)
);

CREATE INDEX IF NOT EXISTS idx_person_name_keys_identity
    ON person_name_keys(identity_id);
"""


def ensure_identity_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(IDENTITY_SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(persons)")}
    if "identity_id" not in cols:
        conn.execute("ALTER TABLE persons ADD COLUMN identity_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_persons_identity ON persons(identity_id)"
    )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync_person_identities(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Ensure every persons row has a 1:1 identity (id initially = legacy person id).

    Does not auto-merge cross-bureau namesakes — that needs evidence later.
    """
    ensure_identity_schema(conn)
    if not force:
        missing = conn.execute(
            "SELECT COUNT(*) FROM persons WHERE identity_id IS NULL OR identity_id = ''"
        ).fetchone()[0]
        keyed = conn.execute("SELECT COUNT(*) FROM person_name_keys").fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        if missing == 0 and keyed >= people:
            return 0

    now = _now()
    count = 0
    rows = conn.execute("SELECT id, name, bureau_code, identity_id FROM persons").fetchall()
    for row in rows:
        pid = row["id"]
        name = row["name"]
        bureau = row["bureau_code"]
        identity_id = (row["identity_id"] or "").strip() or pid
        conn.execute(
            """
            INSERT INTO person_identities (id, primary_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET primary_name=excluded.primary_name
            """,
            (identity_id, name, now),
        )
        conn.execute(
            """
            INSERT INTO person_name_keys (bureau_code, name, identity_id)
            VALUES (?, ?, ?)
            ON CONFLICT(bureau_code, name) DO UPDATE SET identity_id=excluded.identity_id
            """,
            (bureau, name, identity_id),
        )
        if row["identity_id"] != identity_id:
            conn.execute(
                "UPDATE persons SET identity_id = ? WHERE id = ?",
                (identity_id, pid),
            )
        count += 1
    return count


def resolve_identity_id(
    conn: sqlite3.Connection, *, bureau_code: str, name: str
) -> str | None:
    ensure_identity_schema(conn)
    row = conn.execute(
        "SELECT identity_id FROM person_name_keys WHERE bureau_code = ? AND name = ?",
        (bureau_code, name),
    ).fetchone()
    if row:
        return row["identity_id"]
    return None


def appearances_for_identity(conn: sqlite3.Connection, identity_id: str) -> list[dict[str, Any]]:
    ensure_identity_schema(conn)
    rows = conn.execute(
        """
        SELECT bureau_code, name, identity_id
        FROM person_name_keys WHERE identity_id = ?
        ORDER BY bureau_code, name
        """,
        (identity_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def ensure_identity_for_person(
    conn: sqlite3.Connection, *, bureau_code: str, name: str
) -> str:
    """Get or create identity for a bureau+name pair (defaults to legacy person id)."""
    ensure_identity_schema(conn)
    existing = resolve_identity_id(conn, bureau_code=bureau_code, name=name)
    if existing:
        return existing
    iid = legacy_person_id(bureau_code, name)
    now = _now()
    conn.execute(
        """
        INSERT INTO person_identities (id, primary_name, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (iid, name, now),
    )
    conn.execute(
        """
        INSERT INTO person_name_keys (bureau_code, name, identity_id)
        VALUES (?, ?, ?)
        ON CONFLICT(bureau_code, name) DO UPDATE SET identity_id=excluded.identity_id
        """,
        (bureau_code, name, iid),
    )
    return iid
