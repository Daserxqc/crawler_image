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
from tax_platform.crawler.appointment_detail import parse_date_from_title
from tax_platform.models.entities import NoticeMeta
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.dept_catalog import rebuild_catalogs
from tax_platform.store.events import sync_events_for_source
from tax_platform.store.ingest import person_id
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _notice_issued_on(row) -> date | None:
    """Prefer decision date in title over stale CMS/crawl issued_on."""
    titled = parse_date_from_title(row["title"])
    if titled is not None:
        return titled
    issued = _parse_day(row["issued_on"])
    if issued is not None:
        return issued
    # published_at may exist on notices
    keys = row.keys() if hasattr(row, "keys") else []
    if "published_at" in keys:
        return _parse_day(row["published_at"])
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
            NULL,
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
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(id) DO UPDATE SET
                gender=COALESCE(excluded.gender, persons.gender),
                source_leader_url=COALESCE(excluded.source_leader_url, persons.source_leader_url)
            """,
            (
                pid,
                leader["person_name"],
                leader["bureau_code"],
                leader["gender"],
                leader["source_url"],
            ),
        )
    recompute_persons(conn)


def reparse_appointments(db_path: Path) -> dict[str, int]:
    conn = connect(db_path)
    notices = conn.execute(
        """
        SELECT id, bureau_code, title, source_url, issued_on, published_at, raw_text
        FROM notices
        WHERE raw_text IS NOT NULL AND length(raw_text) > 20
        """
    ).fetchall()

    old_events = conn.execute("SELECT count(*) FROM appointment_events").fetchone()[0]
    seen_ids: set[int] = set()
    inserted = 0
    updated = 0
    skipped_notices = 0
    dates_fixed = 0
    for row in notices:
        issued = _notice_issued_on(row)
        if issued is not None and str(row["issued_on"] or "")[:10] != issued.isoformat():
            conn.execute(
                "UPDATE notices SET issued_on = ? WHERE id = ?",
                (issued.isoformat(), int(row["id"])),
            )
            dates_fixed += 1
        notice = NoticeMeta(
            bureau_code=row["bureau_code"],
            title=row["title"] or "",
            source_url=row["source_url"],
            issued_on=issued,
            raw_text=row["raw_text"] or "",
        )
        events = extract_appointment_events(notice)
        if not events:
            skipped_notices += 1
            continue
        payloads: list[dict] = []
        for event in events:
            if not is_plausible_person_name(event.person_name):
                continue
            payload = asdict(event)
            if isinstance(payload.get("effective_on"), date):
                payload["effective_on"] = payload["effective_on"].isoformat()
            payloads.append(payload)
        if not payloads:
            continue
        stats = sync_events_for_source(
            conn,
            notice_id=int(row["id"]),
            bureau_code=row["bureau_code"],
            source_url=row["source_url"],
            events=payloads,
            drop_orphans=True,
        )
        inserted += stats["inserted"]
        updated += stats["updated"]
        for r in conn.execute(
            "SELECT id FROM appointment_events WHERE source_url = ?",
            (row["source_url"],),
        ):
            seen_ids.add(int(r["id"]))

    orphan_ids = [
        int(r["id"])
        for r in conn.execute("SELECT id FROM appointment_events")
        if int(r["id"]) not in seen_ids
    ]
    if orphan_ids:
        placeholders = ",".join("?" * len(orphan_ids))
        conn.execute(f"DELETE FROM appointment_events WHERE id IN ({placeholders})", orphan_ids)
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
        "dates_fixed": dates_fixed,
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
