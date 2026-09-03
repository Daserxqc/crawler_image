# -*- coding: utf-8 -*-
"""Audit org_post_tenures / appointment_events for merge and duplicate issues."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.posts import post_department_key, _title_allows_multiple_holders


def audit(conn: sqlite3.Connection) -> dict:
    report: dict = {}

    multi_inc = conn.execute(
        """
        SELECT p.id, p.bureau_code, p.department, p.title,
               GROUP_CONCAT(t.person_name, '、') AS names,
               COUNT(*) AS n
        FROM org_posts p
        JOIN org_post_tenures t ON t.post_id = p.id AND t.is_current = 1
        GROUP BY p.id
        HAVING n > 1
        ORDER BY n DESC
        """
    ).fetchall()
    report["multi_incumbent_posts"] = [dict(r) for r in multi_inc]

    zero_day = conn.execute(
        """
        SELECT p.bureau_code, p.department, p.title,
               t.person_name, t.started_on, t.ended_on, t.end_change_type
        FROM org_post_tenures t
        JOIN org_posts p ON p.id = t.post_id
        WHERE t.is_current = 0
          AND t.started_on IS NOT NULL AND t.ended_on IS NOT NULL
          AND t.started_on = t.ended_on
          AND t.end_change_type = 'succeeded'
        ORDER BY p.bureau_code, p.department, t.started_on DESC
        """
    ).fetchall()
    report["zero_day_succeeded_past"] = [dict(r) for r in zero_day]

    dup_events = conn.execute(
        """
        SELECT bureau_code, person_name, department_raw, title_raw, effective_on,
               COUNT(*) AS n, COUNT(DISTINCT source_url) AS urls
        FROM appointment_events
        WHERE action = 'appoint'
        GROUP BY bureau_code, person_name, department_raw, title_raw, effective_on
        HAVING n > 1
        ORDER BY n DESC
        """
    ).fetchall()
    report["duplicate_appoint_rows"] = [dict(r) for r in dup_events]

    same_day_groups = conn.execute(
        """
        SELECT bureau_code, effective_on, department_raw, title_raw,
               COUNT(DISTINCT person_name) AS people,
               COUNT(DISTINCT COALESCE(bureau_name, '')) AS units,
               GROUP_CONCAT(DISTINCT person_name) AS names
        FROM appointment_events
        WHERE action = 'appoint'
          AND department_raw IS NOT NULL AND title_raw IS NOT NULL
        GROUP BY bureau_code, effective_on, department_raw, title_raw
        HAVING people > 1
        ORDER BY people DESC
        """
    ).fetchall()
    suspicious_same_day = [
        dict(r)
        for r in same_day_groups
        if int(r["units"] or 0) <= 1
        and int(r["people"] or 0) > 1
        and not _title_allows_multiple_holders(r["title_raw"])
    ]
    report["same_day_multi_appoint"] = [dict(r) for r in same_day_groups]
    report["same_day_missing_unit_scope"] = suspicious_same_day

    # Events that would still merge without bureau_name scope
    unscoped = conn.execute(
        """
        SELECT id, bureau_code, bureau_name, person_name, department_raw,
               title_raw, effective_on, source_url
        FROM appointment_events
        WHERE action = 'appoint'
          AND bureau_name IS NOT NULL AND bureau_name != ''
          AND department_raw IS NOT NULL
        """
    ).fetchall()
    would_merge: list[dict] = []
    buckets: dict[tuple, list] = defaultdict(list)
    for row in unscoped:
        old_key = (
            row["bureau_code"],
            (row["department_raw"] or "").strip(),
            (row["title_raw"] or "").strip(),
            row["effective_on"] or "",
        )
        new_dept = post_department_key(
            row["bureau_code"], row["department_raw"], row["bureau_name"]
        )
        new_key = (
            row["bureau_code"],
            new_dept,
            (row["title_raw"] or "").strip(),
            row["effective_on"] or "",
        )
        buckets[old_key].append(
            {
                "id": row["id"],
                "person": row["person_name"],
                "bureau_name": row["bureau_name"],
                "new_dept": new_dept,
                "source_url": row["source_url"],
            }
        )
    for old_key, items in buckets.items():
        people = {it["person"] for it in items}
        new_keys = {it["new_dept"] for it in items}
        if len(people) > 1 and len(new_keys) > 1:
            would_merge.append(
                {
                    "old_key": old_key,
                    "people": sorted(people),
                    "scoped_departments": sorted(new_keys),
                    "count": len(items),
                }
            )
    report["fixed_by_unit_scope"] = would_merge

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--json", default="output/post_tenure_audit.json")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    report = audit(conn)
    conn.close()

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("岗位 tenure 审计", args.db)
    print("=" * 50)
    print(f"1. 同一岗位多名现任: {len(report['multi_incumbent_posts'])}")
    for row in report["multi_incumbent_posts"][:10]:
        print(
            f"   [{row['n']}] {row['bureau_code']} | {row['department']} | "
            f"{row['title']} -> {row['names']}"
        )

    print(f"\n2. 零天假历任 (succeeded): {len(report['zero_day_succeeded_past'])}")
    by_b = defaultdict(int)
    for row in report["zero_day_succeeded_past"]:
        by_b[row["bureau_code"]] += 1
    for code, n in sorted(by_b.items(), key=lambda x: -x[1])[:10]:
        print(f"   {code}: {n}")

    print(f"\n3. 重复 appoint 行 (同人同岗同日): {len(report['duplicate_appoint_rows'])}")
    for row in report["duplicate_appoint_rows"][:8]:
        print(
            f"   [{row['n']}] {row['bureau_code']} {row['person_name']} "
            f"{row['department_raw']} {row['effective_on']}"
        )

    print(
        f"\n4. 同日同科室同职务多人 appoint: {len(report['same_day_multi_appoint'])} 组"
    )
    print(
        f"   其中 bureau_name 未区分(仍可疑): "
        f"{len(report['same_day_missing_unit_scope'])} 组"
    )
    for row in report["same_day_missing_unit_scope"][:10]:
        print(
            f"   [{row['people']}] {row['bureau_code']} {row['effective_on']} "
            f"{row['department_raw']} {row['title_raw']} -> {row['names']}"
        )

    print(
        f"\n5. 已被单位 scope 修复的合并风险: {len(report['fixed_by_unit_scope'])} 组"
    )
    print(f"\n完整报告: {out.resolve()}")


if __name__ == "__main__":
    main()
