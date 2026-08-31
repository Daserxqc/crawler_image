"""CLI: queue watch digests and flush the email outbox."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.accounts.notify import run_notify_cycle
from tax_platform.accounts.schema import connect
from tax_platform.store.schema import DEFAULT_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Queue digests but mark outbox as dry_run (no SMTP)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Attempt SMTP send when SMTP_* env vars are set",
    )
    args = parser.parse_args()
    dry_run = not args.send
    if args.dry_run:
        dry_run = True
    conn = connect(args.db)
    try:
        result = run_notify_cycle(dry_run=dry_run, conn=conn)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
