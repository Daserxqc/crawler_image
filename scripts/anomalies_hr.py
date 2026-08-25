"""CLI: scan / list anomalies and apply manual corrections (PR6)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.anomalies import (
    apply_correction,
    ignore_anomaly,
    list_anomalies,
    list_corrections,
    scan_anomalies,
)
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="扫描异常并写入 data_anomalies")
    scan.add_argument("--json", action="store_true")

    lst = sub.add_parser("list", help="列出异常")
    lst.add_argument("--status", default="open")
    lst.add_argument("--kind", default=None)
    lst.add_argument("--bureau", default=None)
    lst.add_argument("--severity", default=None)
    lst.add_argument("--limit", type=int, default=30)
    lst.add_argument("--json", action="store_true")

    fix = sub.add_parser("fix", help="修正一条记录")
    fix.add_argument("--type", dest="target_type", required=True)
    fix.add_argument("--id", dest="target_id", required=True)
    fix.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    fix.add_argument("--delete", action="store_true")
    fix.add_argument("--note", default=None)
    fix.add_argument("--anomaly-id", type=int, default=None)
    fix.add_argument("--json", action="store_true")

    ign = sub.add_parser("ignore", help="忽略一条异常")
    ign.add_argument("--anomaly-id", type=int, required=True)
    ign.add_argument("--note", default=None)

    hist = sub.add_parser("history", help="修正审计日志")
    hist.add_argument("--limit", type=int, default=20)
    hist.add_argument("--json", action="store_true")

    args = parser.parse_args()
    conn = connect(args.db)

    if args.cmd == "scan":
        stats = scan_anomalies(conn=conn)
        print(json.dumps(stats, ensure_ascii=False, indent=2) if args.json else stats)
    elif args.cmd == "list":
        result = list_anomalies(
            status=args.status,
            kind=args.kind,
            bureau_code=args.bureau,
            severity=args.severity,
            limit=args.limit,
            conn=conn,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"total={result['total']}")
            for item in result["items"]:
                print(
                    f"#{item['id']}  [{item['severity']}/{item['kind']}]  "
                    f"{item['target_type']}:{item['target_id']}  "
                    f"{item.get('person_name') or ''}  {item['message']}"
                )
    elif args.cmd == "fix":
        patch: dict[str, str | None] = {}
        for item in args.set:
            if "=" not in item:
                parser.error(f"--set 需要 FIELD=VALUE，收到: {item}")
            key, value = item.split("=", 1)
            patch[key] = None if value == "" else value
        result = apply_correction(
            target_type=args.target_type,
            target_id=args.target_id,
            patch=patch,
            note=args.note,
            anomaly_id=args.anomaly_id,
            delete=args.delete,
            conn=conn,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "ignore":
        print(ignore_anomaly(args.anomaly_id, note=args.note, conn=conn))
    else:
        result = list_corrections(limit=args.limit, conn=conn)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for row in result["items"]:
                print(
                    f"#{row['id']}  {row['target_type']}:{row['target_id']}  "
                    f"{row.get('field_name')}  {row.get('old_value')} -> {row.get('new_value')}  "
                    f"{row.get('note') or ''}"
                )
    conn.close()


if __name__ == "__main__":
    main()
