from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store import get_person_profile, person_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show ONE person profile (debug). For all people use scripts/list_persons.py",
    )
    parser.add_argument("--name", required=True)
    parser.add_argument("--bureau", required=True, help="Bureau code, e.g. pdtax / shanghai")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    profile = get_person_profile(person_id(args.bureau, args.name))
    if profile is None:
        raise SystemExit(f"No profile for {args.bureau}:{args.name}")
    text = json.dumps(profile, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
