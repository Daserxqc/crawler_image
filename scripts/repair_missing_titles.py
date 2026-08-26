# -*- coding: utf-8 -*-
"""Re-split events whose title was lost because 司长 was not recognized."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import split_post
from tax_platform.store.schema import connect


def repair(db: Path) -> dict[str, int]:
    conn = connect(db)
    rows = conn.execute(
        """
        SELECT id, title_raw, department_raw, bureau_name
        FROM appointment_events
        WHERE (title_raw IS NULL OR TRIM(title_raw) = '')
          AND department_raw IS NOT NULL
          AND (
            department_raw LIKE '%司长'
            OR department_raw LIKE '%局长'
            OR department_raw LIKE '%处长'
            OR department_raw LIKE '%主任'
          )
        """
    ).fetchall()
    fixed = 0
    for row in rows:
        bureau, dept, title = split_post(row["department_raw"] or "")
        if not title:
            continue
        conn.execute(
            """
            UPDATE appointment_events
            SET title_raw = ?,
                department_raw = ?,
                bureau_name = COALESCE(?, bureau_name)
            WHERE id = ?
            """,
            (title, dept, bureau, int(row["id"])),
        )
        fixed += 1
    conn.commit()
    conn.close()
    return {"candidates": len(rows), "fixed": fixed}


if __name__ == "__main__":
    print(repair(ROOT / "output" / "tax_hr.db"))
