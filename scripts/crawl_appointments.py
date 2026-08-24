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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public appointment notices.")
    parser.add_argument("--site", default="pdtax", help="Bureau code, e.g. pdtax / shanghai / all")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--output", default="output/appointment_crawl.json")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    result = crawl_appointments(args.site, limit=args.limit, delay=args.delay)
    out = dump_json(args.output, appointments_payload(result))
    logging.info("Wrote %s", out)


if __name__ == "__main__":
    main()
