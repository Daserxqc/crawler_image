# -*- coding: utf-8 -*-
"""Backfill bureau_name on appointment_events and remove semantic duplicates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.event_repair import repair_appointment_events
from tax_platform.store.identity import sync_person_identities
from tax_platform.store.posts import rebuild_org_posts
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-rebuild", action="store_true")
    args = parser.parse_args()

    conn = connect(args.db)
    stats = repair_appointment_events(conn, dry_run=args.dry_run)
    print("repair:", stats)

    if args.dry_run or args.skip_rebuild:
        conn.close()
        return

    n_id = sync_person_identities(conn, force=True)
    post_stats = rebuild_org_posts(conn)
    n_tenure = recompute_persons(conn)
    conn.commit()
    conn.close()
    print(f"rebuilt identities={n_id} posts={post_stats} tenure={n_tenure}")


if __name__ == "__main__":
    main()
