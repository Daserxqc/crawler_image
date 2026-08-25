"""CLI: change feed and post incumbent/history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.search.changes import list_changes, post_archive
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    sub = parser.add_subparsers(dest="cmd", required=True)

    feed = sub.add_parser("feed", help="变动动态流")
    feed.add_argument("--level", default=None)
    feed.add_argument("--bureau", default=None)
    feed.add_argument("--type", dest="change_type", default=None)
    feed.add_argument("--department", default=None)
    feed.add_argument("--name", default=None)
    feed.add_argument("--date-from", default=None)
    feed.add_argument("--date-to", default=None)
    feed.add_argument("--limit", type=int, default=20)
    feed.add_argument("--json", action="store_true")

    post = sub.add_parser("post", help="岗位现任/历任")
    post.add_argument("--bureau", required=True)
    post.add_argument("--department", required=True)
    post.add_argument("--title", default=None)
    post.add_argument("--json", action="store_true")

    args = parser.parse_args()
    conn = connect(args.db)

    if args.cmd == "feed":
        result = list_changes(
            org_level=args.level,
            bureau_code=args.bureau,
            change_type=args.change_type,
            department=args.department,
            name=args.name,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            conn=conn,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"total={result['total']} showing={len(result['items'])}")
            for item in result["items"]:
                print(
                    f"{item.get('effective_on') or '?'}  {item['change_type']:16}  "
                    f"{item['person_name']}  [{item['bureau_code']}]  "
                    f"{item.get('title') or ''}  {item.get('department') or ''}"
                )
                if item.get("source_url"):
                    print(f"  {item['source_url']}")
    else:
        result = post_archive(
            bureau_code=args.bureau,
            department=args.department,
            title=args.title,
            conn=conn,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"{result['bureau_code']} / {result['department']}"
                + (f" / {result['title']}" if result.get("title") else "")
            )
            print(f"现任 ({result['incumbent_count']})")
            for row in result["incumbents"]:
                print(
                    f"  · {row['person_name']}  {row.get('title') or ''}  "
                    f"since={row.get('since') or '?'}"
                )
            print(f"历任片段 ({len(result['past'])})")
            for row in result["past"][:10]:
                print(
                    f"  · {row['person_name']}  {row.get('title') or ''}  "
                    f"{row.get('since') or '?'} → {row.get('ended_on') or '?'}"
                )
    conn.close()


if __name__ == "__main__":
    main()
