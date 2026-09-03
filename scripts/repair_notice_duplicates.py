"""Merge duplicate notice URLs and re-extract events for empty notices."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_date_from_title
from tax_platform.crawler.notice_url import normalize_notice_source_url
from tax_platform.models.entities import NoticeMeta
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.events import sync_events_for_source
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _issued(row) -> date | None:
    return parse_date_from_title(row["title"]) or _parse_day(row["issued_on"]) or _parse_day(
        row["published_at"] if "published_at" in row.keys() else None
    )


def merge_duplicate_notices(conn, *, dry_run: bool = False) -> dict[str, int]:
    rows = conn.execute(
        "SELECT id, bureau_code, title, source_url, issued_on, published_at, raw_text, "
        "length(IFNULL(raw_text,'')) AS raw_len FROM notices"
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        canon = normalize_notice_source_url(row["source_url"])
        if not canon:
            continue
        groups[canon].append(row)

    merged = 0
    rewritten = 0
    touched: set[tuple[str, str]] = set()
    for canon, members in groups.items():
        if len(members) == 1:
            only = members[0]
            if only["source_url"] != canon and not dry_run:
                conn.execute(
                    "UPDATE notices SET source_url = ? WHERE id = ?",
                    (canon, int(only["id"])),
                )
                conn.execute(
                    "UPDATE appointment_events SET source_url = ? WHERE notice_id = ?",
                    (canon, int(only["id"])),
                )
                rewritten += 1
            continue

        scored = []
        for row in members:
            ec = conn.execute(
                "SELECT COUNT(*) FROM appointment_events WHERE notice_id = ?",
                (int(row["id"]),),
            ).fetchone()[0]
            scored.append((ec, int(row["raw_len"] or 0), -int(row["id"]), row))
        scored.sort(reverse=True)
        keep = scored[0][3]
        keep_id = int(keep["id"])
        keep_raw = keep["raw_text"] or ""
        for _ec, _raw_len, _nid, row in scored[1:]:
            other_id = int(row["id"])
            other_raw = row["raw_text"] or ""
            if len(other_raw) > len(keep_raw):
                keep_raw = other_raw
            if dry_run:
                merged += 1
                continue
            for ev in conn.execute(
                "SELECT id, person_name, action, raw_clause FROM appointment_events WHERE notice_id = ?",
                (other_id,),
            ):
                clash = conn.execute(
                    """
                    SELECT id FROM appointment_events
                    WHERE notice_id = ?
                      AND person_name = ?
                      AND action = ?
                      AND COALESCE(raw_clause, '') = COALESCE(?, '')
                    LIMIT 1
                    """,
                    (keep_id, ev["person_name"], ev["action"], ev["raw_clause"]),
                ).fetchone()
                if clash:
                    conn.execute("DELETE FROM appointment_events WHERE id = ?", (int(ev["id"]),))
                else:
                    conn.execute(
                        "UPDATE appointment_events SET notice_id = ?, source_url = ? WHERE id = ?",
                        (keep_id, canon, int(ev["id"])),
                    )
            conn.execute("DELETE FROM notices WHERE id = ?", (other_id,))
            merged += 1
        if not dry_run:
            conn.execute(
                "UPDATE notices SET source_url = ?, raw_text = COALESCE(?, raw_text) WHERE id = ?",
                (canon, keep_raw or None, keep_id),
            )
            conn.execute(
                "UPDATE appointment_events SET source_url = ? WHERE notice_id = ?",
                (canon, keep_id),
            )
            # drop exact event dupes after URL merge
            conn.execute(
                """
                DELETE FROM appointment_events
                WHERE notice_id = ?
                  AND id NOT IN (
                    SELECT MIN(id) FROM appointment_events
                    WHERE notice_id = ?
                    GROUP BY person_name, action, COALESCE(raw_clause, '')
                  )
                """,
                (keep_id, keep_id),
            )
            for r in conn.execute(
                "SELECT bureau_code, person_name FROM appointment_events WHERE notice_id = ?",
                (keep_id,),
            ):
                touched.add((r["bureau_code"], r["person_name"]))
        rewritten += 1

    if not dry_run and touched:
        recompute_persons(conn, touched)
    return {"merged_duplicates": merged, "canonicalized": rewritten}


def reparse_empty_notices(conn, *, dry_run: bool = False, limit: int | None = None) -> dict[str, int]:
    sql = """
        SELECT n.id, n.bureau_code, n.title, n.source_url, n.issued_on, n.published_at, n.raw_text
        FROM notices n
        WHERE length(IFNULL(n.raw_text,'')) > 20
          AND NOT EXISTS (SELECT 1 FROM appointment_events e WHERE e.notice_id = n.id)
        ORDER BY n.id
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    filled = 0
    events_added = 0
    touched: set[tuple[str, str]] = set()
    for row in rows:
        issued = _issued(row)
        notice = NoticeMeta(
            bureau_code=row["bureau_code"],
            title=row["title"] or "",
            source_url=normalize_notice_source_url(row["source_url"]) or row["source_url"],
            issued_on=issued,
            raw_text=row["raw_text"] or "",
        )
        events = [
            ev
            for ev in extract_appointment_events(notice)
            if is_plausible_person_name(ev.person_name)
        ]
        if not events:
            continue
        payloads = []
        for event in events:
            payload = asdict(event)
            if isinstance(payload.get("effective_on"), date):
                payload["effective_on"] = payload["effective_on"].isoformat()
            payload["source_url"] = notice.source_url
            payloads.append(payload)
        filled += 1
        if dry_run:
            events_added += len(payloads)
            continue
        stats = sync_events_for_source(
            conn,
            notice_id=int(row["id"]),
            bureau_code=row["bureau_code"],
            source_url=notice.source_url,
            events=payloads,
            drop_orphans=True,
        )
        events_added += stats["inserted"]
        for ev in payloads:
            touched.add((row["bureau_code"], ev["person_name"]))
    if not dry_run and touched:
        recompute_persons(conn, touched)
    return {"notices_filled": filled, "events_added": events_added, "scanned": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        merge_stats = merge_duplicate_notices(conn, dry_run=args.dry_run)
        fill_stats = reparse_empty_notices(conn, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
        print({**merge_stats, **fill_stats, "dry_run": args.dry_run})
    finally:
        conn.close()


if __name__ == "__main__":
    main()
