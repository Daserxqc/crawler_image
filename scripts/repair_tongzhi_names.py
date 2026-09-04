# -*- coding: utf-8 -*-
"""Repair person names truncated before 「同志」 (e.g. 周立渊 → 立渊)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.integrity_clean import repair_tongzhi_truncated_names
from tax_platform.store.schema import connect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "output" / "tax_hr.db"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        report = repair_tongzhi_truncated_names(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
