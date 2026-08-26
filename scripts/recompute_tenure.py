"""Recompute persisted current tenure for all (or one) persons."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_person_current, recompute_persons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--bureau", default=None)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        if args.bureau and args.name:
            current = recompute_person_current(conn, args.bureau, args.name)
            conn.commit()
            print(json.dumps({"bureau": args.bureau, "name": args.name, "current": current}, ensure_ascii=False))
        else:
            n = recompute_persons(conn)
            conn.commit()
            current_n = conn.execute("SELECT count(*) FROM persons WHERE is_current = 1").fetchone()[0]
            print(json.dumps({"recomputed": n, "is_current": current_n}, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
