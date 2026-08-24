"""List sites whose crawl refresh interval has elapsed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.schedule import DEFAULT_REFRESH_DAYS, refresh_days_for
from tax_platform.crawler.crawl_state import load_crawl_state, sites_due_for_crawl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show bureau sites due for crawl by hierarchy cadence.")
    parser.add_argument("--kind", choices=("leaders", "appointments"), default="leaders")
    parser.add_argument("--level", default=None, help="headquarters / province / city / district")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_crawl_state()
    due = sites_due_for_crawl(args.kind, records=records, level=args.level)
    payload = {
        "kind": args.kind,
        "cadence_days": DEFAULT_REFRESH_DAYS,
        "due_count": len(due),
        "due": [
            {
                "code": site.code,
                "name": site.name,
                "level": site.level,
                "refresh_days": refresh_days_for(site.level, site.refresh_days),
            }
            for site in due
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
