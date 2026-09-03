# -*- coding: utf-8 -*-
"""Crawl appointment list pages that are due for refresh, then ingest into SQLite.

Uses existing crawl_state cadence (default **7 days for all bureau levels**).
Skips notice URLs already in the DB (incremental).

Install a Windows daily checker (recommended — no need to run this by hand)::

    python scripts/install_windows_crawl_task.py

Manual run::

    python scripts/crawl_due_appointments.py --db output/tax_hr.db

Optional::

    python scripts/crawl_due_appointments.py --level district --rebuild-posts
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments
from tax_platform.crawler.job_io import dump_json
from tax_platform.store.ingest import ingest_appointment_results, known_notice_urls
from tax_platform.store.schema import connect


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--limit", type=int, default=0, help="Max new notices per site; 0 = no cap")
    parser.add_argument(
        "--level",
        default=None,
        help="Optional: headquarters / province / city / district",
    )
    parser.add_argument(
        "--site",
        default="all",
        help="Bureau code or 'all' (still filtered by --due-only)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-fetch every list item (disable URL incremental skip)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore crawl_state due window (crawl all selected sites)",
    )
    parser.add_argument(
        "--output",
        default="output/due_appointments.json",
        help="Write crawl payload JSON for audit",
    )
    parser.add_argument(
        "--rebuild-posts",
        action="store_true",
        help="Rebuild org_posts / tenures after ingest (slower)",
    )
    parser.add_argument(
        "--with-leaders",
        action="store_true",
        help="Also refresh due leader-intro pages (分管科室补充，默认关闭)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Crawl and write JSON only; do not ingest",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    db_path = Path(args.db)

    known: set[str] | None = None
    incremental = not args.full
    if incremental:
        conn = connect(db_path)
        try:
            known = known_notice_urls(conn=conn)
        finally:
            conn.close()
        logging.info("Known notice URLs: %s", len(known or []))

    result = crawl_appointments(
        args.site,
        limit=args.limit,
        delay=args.delay,
        due_only=not args.force,
        level=args.level,
        incremental=incremental,
        known_urls=known,
        record_state=True,
    )
    payload = appointments_payload(result)
    out_path = dump_json(args.output, payload)

    site_count = len(payload) if isinstance(payload, list) else 1
    notices = 0
    events = 0
    skipped = 0
    failed = 0
    rows = payload if isinstance(payload, list) else [payload]
    for item in rows:
        notices += len(item.get("notices") or [])
        events += len(item.get("events") or [])
        skipped += int(item.get("skipped") or 0)
        failed += len(item.get("failed") or [])

    summary: dict[str, object] = {
        "sites": site_count,
        "notices": notices,
        "events": events,
        "skipped_known": skipped,
        "failed": failed,
        "crawl_file": str(out_path),
        "db": str(db_path.resolve()),
        "due_only": not args.force,
        "ingested_events": 0,
    }

    if args.dry_run:
        logging.info("Dry-run: skip ingest")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    ingested = ingest_appointment_results(payload)
    summary["ingested_events"] = ingested
    logging.info("Ingested %s new/updated appointment events", ingested)

    if args.with_leaders:
        from tax_platform.crawler.leader_job import crawl_leaders, leaders_payload
        from tax_platform.store.ingest import ingest_leader_results

        leader_result = crawl_leaders(
            args.site,
            delay=args.delay,
            due_only=not args.force,
            level=args.level,
        )
        leader_payload = leaders_payload(leader_result)
        dump_json("output/due_leaders.json", leader_payload)
        leader_n = ingest_leader_results(leader_payload)
        summary["ingested_leaders"] = leader_n
        logging.info("Ingested leader duties from due sites: %s", leader_n)

    if args.rebuild_posts:
        from tax_platform.store.identity import sync_person_identities
        from tax_platform.store.posts import rebuild_org_posts

        conn = connect(db_path)
        try:
            sync_person_identities(conn)
            stats = rebuild_org_posts(conn)
            conn.commit()
            summary["posts_rebuild"] = stats
            logging.info("Rebuilt org_posts: %s", stats)
        finally:
            conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
