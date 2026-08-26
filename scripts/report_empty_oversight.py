"""Report leader rows with empty oversight departments and suggest re-crawls."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.schema import connect


def _deps_empty(raw: str | None) -> bool:
    if raw is None or not str(raw).strip():
        return True
    text = str(raw).strip()
    if text in {"[]", "null", "None"}:
        return True
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return not data


def report_empty_oversight(db_path: Path) -> dict:
    conn = connect(db_path)
    try:
        by_bureau: dict[str, dict] = defaultdict(
            lambda: {"leaders": 0, "empty_oversight": 0, "names": []}
        )
        for row in conn.execute(
            "SELECT bureau_code, person_name, duty_summary, departments_json FROM leader_duties"
        ):
            bureau = row["bureau_code"]
            by_bureau[bureau]["leaders"] += 1
            if _deps_empty(row["departments_json"]):
                by_bureau[bureau]["empty_oversight"] += 1
                if len(by_bureau[bureau]["names"]) < 8:
                    by_bureau[bureau]["names"].append(row["person_name"])

        empty_bureaus = sorted(
            (
                {
                    "bureau_code": code,
                    "leaders": meta["leaders"],
                    "empty_oversight": meta["empty_oversight"],
                    "empty_ratio": round(meta["empty_oversight"] / meta["leaders"], 2)
                    if meta["leaders"]
                    else 0,
                    "sample_names": meta["names"],
                    "suggest": f"python scripts/crawl_leaders.py --site {code}",
                }
                for code, meta in by_bureau.items()
                if meta["empty_oversight"] > 0
            ),
            key=lambda x: (-x["empty_oversight"], x["bureau_code"]),
        )
        return {
            "bureaus_with_leaders": len(by_bureau),
            "bureaus_with_empty_oversight": len(empty_bureaus),
            "items": empty_bureaus,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()
    report = report_empty_oversight(args.db)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(
        f"bureaus_with_leaders={report['bureaus_with_leaders']} "
        f"empty_oversight_bureaus={report['bureaus_with_empty_oversight']}"
    )
    for item in report["items"]:
        print(
            f"{item['bureau_code']}\tempty={item['empty_oversight']}/{item['leaders']}\t"
            f"{item['suggest']}"
        )


if __name__ == "__main__":
    main()
