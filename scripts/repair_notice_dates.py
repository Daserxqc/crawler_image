# -*- coding: utf-8 -*-
"""Backfill notice/event dates from title decision day (…（2025年12月11日）)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_detail import parse_date_from_title
from tax_platform.store.schema import connect


def repair_dates(db_path: Path) -> dict[str, int]:
    conn = connect(db_path)
    notices_fixed = 0
    events_fixed = 0
    rows = conn.execute(
        "SELECT id, title, source_url, issued_on, published_at FROM notices"
    ).fetchall()
    for row in rows:
        titled = parse_date_from_title(row["title"])
        new_day = titled.isoformat() if titled is not None else None
        if new_day is None:
            pub = str(row["published_at"] or "")[:10]
            old = str(row["issued_on"] or "")[:10]
            # CMS/目录迁移日常见为整页同一天；有发布时间则改用发布日
            if (
                len(pub) == 10
                and pub[0:4].isdigit()
                and old
                and pub != old
                and old >= "2026-08-01"  # recent crawl/move artifact window
            ):
                new_day = pub
        if new_day is None:
            continue
        old = str(row["issued_on"] or "")[:10]
        if old != new_day:
            conn.execute(
                "UPDATE notices SET issued_on = ? WHERE id = ?",
                (new_day, int(row["id"])),
            )
            notices_fixed += 1
        cur = conn.execute(
            """
            UPDATE appointment_events
            SET effective_on = ?
            WHERE source_url = ?
              AND COALESCE(substr(effective_on, 1, 10), '') != ?
            """,
            (new_day, row["source_url"], new_day),
        )
        events_fixed += cur.rowcount
    conn.commit()
    conn.close()
    return {"notices_fixed": notices_fixed, "events_fixed": events_fixed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "output" / "tax_hr.db")
    args = parser.parse_args()
    stats = repair_dates(args.db)
    print(stats)


if __name__ == "__main__":
    main()
