# -*- coding: utf-8 -*-
"""Repair appointment events whose person_name starts with 去 (免去… misparse)."""

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
from tax_platform.store.events import sync_events_for_source
from tax_platform.store.identity import sync_person_identities
from tax_platform.store.posts import rebuild_org_posts
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_person_current


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    args = parser.parse_args()
    conn = connect(args.db)

    bad_urls = [
        r["source_url"]
        for r in conn.execute(
            """
            SELECT DISTINCT source_url FROM appointment_events
            WHERE person_name LIKE '去%' OR person_name LIKE '免%' OR person_name LIKE '命%'
            """
        )
    ]
    print(f"notices_to_reparse={len(bad_urls)}")

    fixed = 0
    touched_keys: set[tuple[str, str]] = set()
    for url in bad_urls:
        row = conn.execute(
            """
            SELECT id, bureau_code, title, source_url, issued_on, raw_text
            FROM notices WHERE source_url = ?
            """,
            (url,),
        ).fetchone()
        if row is None or not (row["raw_text"] or "").strip():
            conn.execute(
                """
                DELETE FROM appointment_events
                WHERE source_url = ?
                  AND (person_name LIKE '去%' OR person_name LIKE '免%' OR person_name LIKE '命%')
                """,
                (url,),
            )
            continue
        issued = parse_date_from_title(row["title"]) or _parse_day(row["issued_on"])
        notice = NoticeMeta(
            bureau_code=row["bureau_code"],
            title=row["title"] or "",
            source_url=row["source_url"],
            issued_on=issued,
            raw_text=row["raw_text"] or "",
        )
        events = [
            e
            for e in extract_appointment_events(notice)
            if is_plausible_person_name(e.person_name)
        ]
        payloads = []
        for event in events:
            payload = asdict(event)
            if isinstance(payload.get("effective_on"), date):
                payload["effective_on"] = payload["effective_on"].isoformat()
            payloads.append(payload)
            touched_keys.add((row["bureau_code"], event.person_name))
        sync_events_for_source(
            conn,
            notice_id=int(row["id"]),
            bureau_code=row["bureau_code"],
            source_url=row["source_url"],
            events=payloads,
            drop_orphans=True,
        )
        fixed += 1

    left = conn.execute(
        "SELECT COUNT(*) FROM appointment_events WHERE person_name LIKE '去%'"
    ).fetchone()[0]
    if left:
        conn.execute("DELETE FROM appointment_events WHERE person_name LIKE '去%'")
        print(f"deleted_remaining_go_names={left}")

    conn.execute("DELETE FROM persons WHERE name LIKE '去%' OR name LIKE '免%' OR name LIKE '命%'")
    conn.execute(
        "DELETE FROM person_name_keys WHERE name LIKE '去%' OR name LIKE '免%' OR name LIKE '命%'"
    )
    conn.execute(
        """
        DELETE FROM person_identities
        WHERE primary_name LIKE '去%' OR primary_name LIKE '免%' OR primary_name LIKE '命%'
        """
    )

    if touched_keys:
        for pair in sorted(touched_keys):
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                continue
            bureau, name = pair[0], pair[1]
            if name:
                recompute_person_current(conn, bureau, name)

    sync_person_identities(conn, force=True)
    posts = rebuild_org_posts(conn)
    conn.commit()
    left2 = conn.execute(
        "SELECT COUNT(*) FROM appointment_events WHERE person_name LIKE '去%'"
    ).fetchone()[0]
    sample = conn.execute(
        """
        SELECT person_name, action, department_raw, title_raw FROM appointment_events
        WHERE person_name = '许绍华' LIMIT 5
        """
    ).fetchall()
    print(f"reparsed={fixed}; go_names_left={left2}; posts={posts}")
    for s in sample:
        print(" 许绍华", s["action"], s["department_raw"], s["title_raw"])
    conn.close()


if __name__ == "__main__":
    main()
