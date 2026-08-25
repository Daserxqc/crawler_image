from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.province_discovery import discover_province
from tax_platform.config.province_urls_io import (
    DEFAULT_REGISTRY,
    entry_to_override,
    merge_discovery_entry,
    merge_override,
    write_overrides,
)
from tax_platform.config.sites_provinces import PROVINCE_SUBDOMAINS
from tax_platform.crawler.appointment_job import crawl_appointments_site
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.crawler.leader_job import crawl_leaders_site
from tax_platform.store import ingest_appointment_results, ingest_leader_results, list_persons

PROVINCE_CODES = [code for _sub, _region, code in PROVINCE_SUBDOMAINS if code != "shanghai"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover URLs, crawl, and ingest one province at a time.")
    parser.add_argument("code", nargs="?", help="Province code, e.g. guangdong")
    parser.add_argument("--list", action="store_true", help="List province codes")
    parser.add_argument("--skip-discover", action="store_true", help="Use existing URL config only")
    parser.add_argument("--skip-leaders", action="store_true")
    parser.add_argument("--skip-appointments", action="store_true")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--appt-limit", type=int, default=0, help="0 = no cap on appointment notices")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--output-dir", default="output/provinces")
    return parser.parse_args()


def _subdomain_for(code: str) -> str:
    for subdomain, _region, item_code in PROVINCE_SUBDOMAINS:
        if item_code == code:
            return subdomain
    raise KeyError(f"Unknown province code: {code}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    if args.list:
        print("\n".join(PROVINCE_CODES))
        return

    if not args.code:
        raise SystemExit("Province code required. Example: python scripts/crawl_province.py guangdong")

    code = args.code.strip().lower()
    if code not in PROVINCE_CODES:
        raise SystemExit(f"Unknown province '{code}'. Use --list to see codes.")

    summary: dict[str, object] = {"code": code}
    registry = Path(args.registry)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_discover:
        logging.info("Discovering URLs for %s ...", code)
        discovery = discover_province(code, _subdomain_for(code), delay=args.delay)
        entry = asdict(discovery)
        merge_discovery_entry(entry, path=registry)
        summary["discovery"] = entry

        override = entry_to_override(entry)
        if override and (entry.get("leader_intro_url") or entry.get("appointment_list_url")):
            write_overrides(merge_override(code, override))
            logging.info("Applied URL override for %s", code)
        elif override:
            logging.warning("Discovery for %s only found home page; keeping default paths", code)
        else:
            logging.warning("No usable URLs discovered for %s", code)

    if not args.skip_leaders:
        logging.info("Crawling leaders for %s ...", code)
        try:
            leader_result = crawl_leaders_site(code, delay=args.delay)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Leader crawl failed for %s: %s", code, exc)
            from tax_platform.config.sites import get_site
            from tax_platform.crawler.leader_job import LeaderCrawlResult

            leader_result = LeaderCrawlResult(
                bureau=code,
                hub_url=get_site(code).leader_intro_url,
                page_count=0,
                failed=[{"url": get_site(code).leader_intro_url, "error": str(exc)}],
            )
        leader_payload = serialize_crawl_result(leader_result)
        leader_path = dump_json(out_dir / f"{code}_leaders.json", leader_payload)
        leader_count = ingest_leader_results(leader_payload)
        summary["leaders_file"] = str(leader_path)
        summary["leaders"] = len(leader_result.leaders)
        summary["leaders_failed"] = len(leader_result.failed)
        summary["leaders_ingested"] = leader_count
        logging.info("Leaders %s: %s people, %s failed", code, len(leader_result.leaders), len(leader_result.failed))

    if not args.skip_appointments:
        logging.info("Crawling appointments for %s ...", code)
        try:
            appt_result = crawl_appointments_site(code, limit=args.appt_limit, delay=args.delay)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Appointment crawl failed for %s: %s", code, exc)
            from tax_platform.config.sites import get_site
            from tax_platform.crawler.appointment_job import AppointmentCrawlResult

            appt_result = AppointmentCrawlResult(
                bureau=code,
                list_url=get_site(code).appointment_list_url,
                list_count=0,
                failed=[{"url": get_site(code).appointment_list_url, "error": str(exc)}],
            )
        appt_payload = serialize_crawl_result(appt_result)
        appt_path = dump_json(out_dir / f"{code}_appointments.json", appt_payload)
        new_events = ingest_appointment_results(appt_payload)
        summary["appointments_file"] = str(appt_path)
        summary["notices"] = len(appt_result.notices)
        summary["events"] = len(appt_result.events)
        summary["appointments_failed"] = len(appt_result.failed)
        summary["new_events_ingested"] = new_events
        logging.info(
            "Appointments %s: %s notices, %s events, %s failed",
            code,
            len(appt_result.notices),
            len(appt_result.events),
            len(appt_result.failed),
        )

    summary["persons_in_db"] = len(list_persons())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
