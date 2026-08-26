"""Appointment-event upsert helpers shared by ingest and reparse."""

from __future__ import annotations

import sqlite3
from typing import Any


def event_unique_key(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        event.get("source_url") or "",
        event.get("person_name") or "",
        event.get("action") or "",
        event.get("raw_clause") or "",
    )


def upsert_appointment_event(
    db: sqlite3.Connection,
    *,
    notice_id: int,
    bureau_code: str,
    event: dict[str, Any],
) -> tuple[int, bool]:
    """Insert or update one event by unique key. Returns ``(event_id, inserted)``."""
    source_url = event.get("source_url") or ""
    person_name = event.get("person_name") or ""
    action = event.get("action") or ""
    clause = event.get("raw_clause") or ""
    existing = db.execute(
        """
        SELECT id FROM appointment_events
        WHERE source_url = ?
          AND person_name = ?
          AND action = ?
          AND COALESCE(raw_clause, '') = ?
        """,
        (source_url, person_name, action, clause),
    ).fetchone()
    values = (
        notice_id,
        bureau_code,
        person_name,
        action,
        event.get("bureau_name"),
        event.get("department_raw"),
        event.get("title_raw"),
        event.get("probation_years"),
        event.get("effective_on"),
        event.get("notice_title"),
        event.get("raw_clause"),
        source_url,
    )
    if existing:
        eid = int(existing["id"])
        db.execute(
            """
            UPDATE appointment_events SET
                notice_id=?, bureau_code=?, person_name=?, action=?,
                bureau_name=?, department_raw=?, title_raw=?,
                probation_years=?, effective_on=?, notice_title=?,
                raw_clause=?, source_url=?
            WHERE id=?
            """,
            values + (eid,),
        )
        return eid, False
    cursor = db.execute(
        """
        INSERT INTO appointment_events (
            notice_id, bureau_code, person_name, action, bureau_name,
            department_raw, title_raw, probation_years, effective_on,
            notice_title, raw_clause, source_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
    return int(cursor.lastrowid), True


def sync_events_for_source(
    db: sqlite3.Connection,
    *,
    notice_id: int,
    bureau_code: str,
    source_url: str,
    events: list[dict[str, Any]],
    drop_orphans: bool = True,
) -> dict[str, int]:
    """Upsert events for one notice URL; optionally drop orphans under that URL."""
    seen: set[int] = set()
    inserted = 0
    updated = 0
    for event in events:
        if not event.get("person_name"):
            continue
        payload = {**event, "source_url": event.get("source_url") or source_url}
        eid, was_insert = upsert_appointment_event(
            db,
            notice_id=notice_id,
            bureau_code=bureau_code,
            event=payload,
        )
        seen.add(eid)
        if was_insert:
            inserted += 1
        else:
            updated += 1

    removed = 0
    if drop_orphans:
        rows = db.execute(
            "SELECT id FROM appointment_events WHERE source_url = ?",
            (source_url,),
        ).fetchall()
        orphan_ids = [int(r["id"]) for r in rows if int(r["id"]) not in seen]
        if orphan_ids:
            placeholders = ",".join("?" * len(orphan_ids))
            db.execute(
                f"DELETE FROM appointment_events WHERE id IN ({placeholders})",
                orphan_ids,
            )
            db.execute(
                f"""
                DELETE FROM data_anomalies
                WHERE target_type = 'appointment_event'
                  AND target_id IN ({placeholders})
                """,
                [str(i) for i in orphan_ids],
            )
            removed = len(orphan_ids)
    return {"inserted": inserted, "updated": updated, "removed": removed, "kept": len(seen)}
