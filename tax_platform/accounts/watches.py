"""Watch people or bureaus for change alerts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from tax_platform.accounts.schema import connect
from tax_platform.config.sites import get_site

ALLOWED_TYPES = {"person", "bureau"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def add_watch(
    user_id: int,
    *,
    target_type: str,
    target_id: str,
    label: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    target_type = (target_type or "").strip()
    target_id = (target_id or "").strip()
    if target_type not in ALLOWED_TYPES:
        raise ValueError("target_type must be person|bureau")
    if not target_id:
        raise ValueError("target_id is required")

    display = (label or "").strip() or None
    if display is None and target_type == "bureau":
        try:
            display = get_site(target_id).name
        except KeyError:
            display = target_id
    if display is None and target_type == "person":
        display = target_id.split(":", 1)[-1]

    try:
        cur = db.execute(
            """
            INSERT INTO watches (user_id, target_type, target_id, label, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, target_type, target_id, display, _iso_now()),
        )
        watch_id = int(cur.lastrowid)
    except sqlite3.IntegrityError:
        row = db.execute(
            """
            SELECT id FROM watches
            WHERE user_id = ? AND target_type = ? AND target_id = ?
            """,
            (user_id, target_type, target_id),
        ).fetchone()
        watch_id = int(row["id"])
        db.execute(
            "UPDATE watches SET label = COALESCE(?, label) WHERE id = ?",
            (display, watch_id),
        )

    # Seed notify cursor so historical events are not emailed on first watch.
    max_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM appointment_events").fetchone()[0]
    db.execute(
        """
        INSERT INTO notify_cursor (user_id, last_event_id, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, int(max_id), _iso_now()),
    )
    db.commit()
    out = get_watch(watch_id, conn=db)
    if owns:
        db.close()
    return out


def get_watch(watch_id: int, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    row = db.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    if row is None:
        if owns:
            db.close()
        raise KeyError(f"watch {watch_id} not found")
    out = dict(row)
    if owns:
        db.close()
    return out


def list_watches(user_id: int, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    rows = db.execute(
        """
        SELECT * FROM watches
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    ).fetchall()
    items = [dict(r) for r in rows]
    if owns:
        db.close()
    return {"total": len(items), "items": items}


def remove_watch(
    user_id: int,
    watch_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    row = db.execute(
        "SELECT id FROM watches WHERE id = ? AND user_id = ?",
        (watch_id, user_id),
    ).fetchone()
    if row is None:
        if owns:
            db.close()
        raise KeyError(f"watch {watch_id} not found")
    db.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
    db.commit()
    if owns:
        db.close()
    return {"ok": True, "id": watch_id}


def is_watching(
    user_id: int,
    *,
    target_type: str,
    target_id: str,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    owns = conn is None
    db = conn or connect()
    row = db.execute(
        """
        SELECT * FROM watches
        WHERE user_id = ? AND target_type = ? AND target_id = ?
        """,
        (user_id, target_type, target_id),
    ).fetchone()
    out = dict(row) if row else None
    if owns:
        db.close()
    return out
