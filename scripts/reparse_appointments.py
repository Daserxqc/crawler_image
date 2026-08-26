"""Re-extract appointment events from stored notice raw_text, then rebuild catalogs."""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.models.entities import NoticeMeta
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.dept_catalog import rebuild_catalogs
from tax_platform.store.ingest import person_id
from tax_platform.store.schema import connect


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _rebuild_persons(conn) -> None:
    """Rebuild persons from appointment events + leaders (appointment-only people kept)."""
    conn.execute("DELETE FROM persons")
    conn.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        SELECT
            bureau_code || ':' || person_name,
            person_name,
            bureau_code,
            NULL,
            (
                SELECT e2.title_raw FROM appointment_events e2
                WHERE e2.bureau_code = e.bureau_code AND e2.person_name = e.person_name
                ORDER BY COALESCE(e2.effective_on, '') DESC, e2.id DESC
                LIMIT 1
            ),
            NULL
        FROM (
            SELECT DISTINCT bureau_code, person_name FROM appointment_events
        ) e
        """
    )
    for leader in conn.execute(
        "SELECT bureau_code, person_name, gender, title_raw, source_url FROM leader_duties"
    ):
        if not is_plausible_person_name(leader["person_name"]):
            continue
        pid = person_id(leader["bureau_code"], leader["person_name"])
        conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                gender=COALESCE(excluded.gender, persons.gender),
                title_current=COALESCE(excluded.title_current, persons.title_current),
                source_leader_url=COALESCE(excluded.source_leader_url, persons.source_leader_url)
            """,
            (
                pid,
                leader["person_name"],
                leader["bureau_code"],
                leader["gender"],
                leader["title_raw"],
                leader["source_url"],
            ),
        )


def reparse_appointments(db_path: Path) -> dict[str, int]:
    conn = connect(db_path)
    notices = conn.execute(
        """
        SELECT id, bureau_code, title, source_url, issued_on, raw_text
        FROM notices
        WHERE raw_text IS NOT NULL AND length(raw_text) > 20
        """
    ).fetchall()

    old_events = conn.execute("SELECT count(*) FROM appointment_events").fetchone()[0]

    # Upsert by unique key so existing event ids (and anomaly target_id) stay stable.
    seen_ids: set[int] = set()
    inserted = 0
    updated = 0
    skipped_notices = 0
    for row in notices:
        notice = NoticeMeta(
            bureau_code=row["bureau_code"],
            title=row["title"] or "",
            source_url=row["source_url"],
            issued_on=_parse_day(row["issued_on"]),
            raw_text=row["raw_text"] or "",
        )
        events = extract_appointment_events(notice)
        if not events:
            skipped_notices += 1
            continue
        for event in events:
            if not is_plausible_person_name(event.person_name):
                continue
            payload = asdict(event)
            if isinstance(payload.get("effective_on"), date):
                payload["effective_on"] = payload["effective_on"].isoformat()
            clause = payload.get("raw_clause") or ""
            existing = conn.execute(
                """
                SELECT id FROM appointment_events
                WHERE source_url = ?
                  AND person_name = ?
                  AND action = ?
                  AND COALESCE(raw_clause, '') = ?
                """,
                (
                    payload["source_url"],
                    payload["person_name"],
                    payload["action"],
                    clause,
                ),
            ).fetchone()
            values = (
                row["id"],
                row["bureau_code"],
                payload["person_name"],
                payload["action"],
                payload["bureau_name"],
                payload["department_raw"],
                payload["title_raw"],
                payload["probation_years"],
                payload["effective_on"],
                payload["notice_title"],
                payload["raw_clause"],
                payload["source_url"],
            )
            try:
                if existing:
                    eid = int(existing["id"])
                    conn.execute(
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
                    seen_ids.add(eid)
                    updated += 1
                else:
                    cursor = conn.execute(
                        """
                        INSERT INTO appointment_events (
                            notice_id, bureau_code, person_name, action, bureau_name,
                            department_raw, title_raw, probation_years, effective_on,
                            notice_title, raw_clause, source_url
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    seen_ids.add(int(cursor.lastrowid))
                    inserted += 1
            except Exception:
                continue

    orphan_ids = [
        int(r["id"])
        for r in conn.execute("SELECT id FROM appointment_events")
        if int(r["id"]) not in seen_ids
    ]
    if orphan_ids:
        placeholders = ",".join("?" * len(orphan_ids))
        conn.execute(f"DELETE FROM appointment_events WHERE id IN ({placeholders})", orphan_ids)
        # Drop open anomalies that pointed at removed event rows.
        conn.execute(
            f"""
            DELETE FROM data_anomalies
            WHERE target_type = 'appointment_event'
              AND target_id IN ({placeholders})
            """,
            [str(i) for i in orphan_ids],
        )

    _rebuild_persons(conn)

    conn.commit()
    catalog = rebuild_catalogs(conn=conn)
    bad_left = conn.execute(
        """
        SELECT count(*) FROM appointment_events
        WHERE person_name IN ('省税务局','市税务局','人事')
           OR person_name LIKE '命%'
           OR person_name LIKE '任命%'
           OR person_name LIKE '关于%'
        """
    ).fetchone()[0]
    people = conn.execute(
        "SELECT count(DISTINCT bureau_code || ':' || person_name) FROM appointment_events"
    ).fetchone()[0]
    conn.close()
    return {
        "notices": len(notices),
        "notices_without_events": skipped_notices,
        "old_events": old_events,
        "events_inserted": inserted,
        "events_updated": updated,
        "events_removed": len(orphan_ids),
        "new_events": inserted + updated,
        "event_people": people,
        "bad_names_left": bad_left,
        "dept_catalog": catalog["dept_rows"],
        "title_catalog": catalog["title_rows"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    args = parser.parse_args()
    stats = reparse_appointments(args.db)
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
