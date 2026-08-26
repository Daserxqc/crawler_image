"""Dedupe leader_duties: keep richest row per (bureau_code, person_name)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tax_platform.store.schema import connect


def richness(row) -> tuple:
    try:
        deps = json.loads(row["departments_json"] or "[]")
    except Exception:
        deps = []
    return (
        len(deps),
        1 if (row["duty_summary"] or "").strip() else 0,
        len(row["title_raw"] or ""),
        -int(row["id"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--bureau", default=None)
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        sql = "SELECT * FROM leader_duties"
        params: list = []
        if args.bureau:
            sql += " WHERE bureau_code=?"
            params.append(args.bureau)
        rows = conn.execute(sql, params).fetchall()
        best: dict[tuple[str, str], object] = {}
        for row in rows:
            key = (row["bureau_code"], row["person_name"])
            prev = best.get(key)
            if prev is None or richness(row) > richness(prev):
                best[key] = row
        keep_ids = {int(r["id"]) for r in best.values()}
        before = len(rows)
        deleted = 0
        for row in rows:
            if int(row["id"]) not in keep_ids:
                conn.execute("DELETE FROM leader_duties WHERE id=?", (row["id"],))
                deleted += 1
        conn.commit()
        print(json.dumps({"before": before, "kept": len(keep_ids), "deleted": deleted}))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
