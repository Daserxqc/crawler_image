# -*- coding: utf-8 -*-
"""Clean integrity noise: orphan bureaus, STA→local remap, dedupe, rebuild posts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.event_repair import repair_appointment_events
from tax_platform.store.identity import sync_person_identities
from tax_platform.store.integrity_clean import (
    ORPHAN_BUREAU_REMAP,
    prune_persons_without_source,
    purge_orphan_bureau_codes,
    remap_sta_local_events,
)
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
    report: dict = {"db": str(Path(args.db).resolve()), "dry_run": args.dry_run}

    report["orphan_purge"] = purge_orphan_bureau_codes(conn, dry_run=args.dry_run)
    report["sta_remap"] = remap_sta_local_events(conn, dry_run=args.dry_run)
    report["event_repair"] = repair_appointment_events(conn, dry_run=args.dry_run)

    if args.dry_run or args.skip_rebuild:
        # Still report how many sta orphans would prune after remap (estimate via dry remap count).
        report["persons_pruned"] = prune_persons_without_source(
            conn,
            bureau_codes=["sta", *sorted(set(ORPHAN_BUREAU_REMAP))],
            dry_run=True,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        conn.close()
        return

    report["persons_pruned"] = prune_persons_without_source(
        conn,
        bureau_codes=["sta", *sorted(set(ORPHAN_BUREAU_REMAP))],
        dry_run=False,
    )
    report["identities"] = sync_person_identities(conn, force=True)
    report["posts"] = rebuild_org_posts(conn)
    report["tenure"] = recompute_persons(conn)
    conn.commit()
    conn.close()

    out = Path("output/_integrity_clean_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
