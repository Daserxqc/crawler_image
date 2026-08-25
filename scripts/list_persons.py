from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store import export_profiles, list_persons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List or export all person profiles from the database.")
    parser.add_argument("--bureau", default=None, help="Filter by bureau code, e.g. pdtax / shanghai / all")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Export full profiles with appointment history and notice links",
    )
    parser.add_argument("--output", default=None, help="Write JSON to file instead of stdout")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bureau = None if args.bureau in (None, "all") else args.bureau
    if args.full:
        payload = export_profiles(bureau_code=bureau)
    else:
        payload = list_persons(bureau_code=bureau)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Wrote {len(payload)} records to {Path(args.output).resolve()}")
    else:
        print(text)


if __name__ == "__main__":
    main()
