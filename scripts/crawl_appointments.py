from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments
from tax_platform.crawler.job_io import dump_json
from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public appointment notices.")
    parser.add_argument("--site", default="pdtax", help="Bureau code, e.g. pdtax / shanghai / all")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max notices per site; 0 = no cap (all links on the list page)",
    )
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--output", default="output/appointment_crawl.json")
    parser.add_argument(
        "--due-only",
        action="store_true",
        help="Only crawl sites whose refresh interval has elapsed",
    )
    parser.add_argument(
        "--level",
        default=None,
        help="Optional filter: headquarters / province / city / district",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-fetch every list item (disable notice-level incremental skip)",
    )
    parser.add_argument(
        "--db",
        default="output/tax_hr.db",
        help="SQLite path used to load known notice URLs for incremental crawl",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    known: set[str] | None = None
    incremental = not args.full
    if incremental:
        conn = connect(args.db)
        try:
            known = known_notice_urls(conn=conn)
        finally:
            conn.close()
        logging.info("Incremental crawl: %s known notice URLs from %s", len(known), args.db)
    result = crawl_appointments(
        args.site,
        limit=args.limit,
        delay=args.delay,
        due_only=args.due_only,
        level=args.level,
        incremental=incremental,
        known_urls=known,
    )
    out = dump_json(args.output, appointments_payload(result))
    logging.info("Wrote %s", out)


if __name__ == "__main__":
    main()
