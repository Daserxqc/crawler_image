"""Rebuild hierarchy–department and title catalogs from tax_hr.db."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.dept_catalog import department_level_matrix, rebuild_catalogs
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("output/tax_hr.db"),
        help="SQLite path",
    )
    parser.add_argument(
        "--matrix",
        action="store_true",
        help="Print province-only / district-only department summary",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional JSON dump of level matrix",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    stats = rebuild_catalogs(conn=conn)
    print(f"dept_catalog rows: {stats['dept_rows']}")
    print(f"title_catalog rows: {stats['title_rows']}")

    if args.matrix or args.out:
        matrix = department_level_matrix(conn=conn)
        province_only = [m for m in matrix if m["province_only"]]
        district_only = [m for m in matrix if m["district_only"]]
        cross = [m for m in matrix if m["cross_level"]]
        print(f"province-only departments: {len(province_only)}")
        print(f"district-only departments: {len(district_only)}")
        print(f"cross-level departments: {len(cross)}")
        if args.matrix:
            print("\n--- province-only (sample) ---")
            for row in province_only[:25]:
                print(f"  {row['canonical_name']}  n={row['total_count']}")
            print("\n--- district-only (sample) ---")
            for row in district_only[:25]:
                print(f"  {row['canonical_name']}  n={row['total_count']}")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(matrix, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"wrote {args.out}")
    conn.close()


if __name__ == "__main__":
    main()
