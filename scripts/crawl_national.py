from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import list_sites
from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import crawl_leaders, leaders_payload
from tax_platform.store import connect, ingest_appointment_results, ingest_leader_results, list_persons


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl + ingest headquarters and all provincial bureaus.")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--skip-leaders", action="store_true")
    parser.add_argument("--skip-appointments", action="store_true")
    parser.add_argument("--leaders-out", default="output/national_leaders.json")
    parser.add_argument("--appointments-out", default="output/national_appointments.json")
    parser.add_argument("--db", default="output/tax_hr.db")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    targets = list_sites("headquarters") + list_sites("province")
    logging.info("National crawl: %s sites (HQ + provinces)", len(targets))

    summary: dict[str, object] = {"sites": len(targets), "targets": [site.code for site in targets]}

    if not args.skip_leaders:
        leaders = crawl_leaders("national", delay=args.delay, record_state=True)
        payload = leaders_payload(leaders)
        leader_path = dump_json(args.leaders_out, payload)
        leader_rows = sum(len(item.get("leaders", [])) for item in payload) if isinstance(payload, list) else 0
        failed = sum(len(item.get("failed", [])) for item in payload) if isinstance(payload, list) else 0
        ingest_leader_results(payload)
        summary["leaders_file"] = str(leader_path)
        summary["leaders_rows"] = leader_rows
        summary["leaders_failed"] = failed
        logging.info("Leaders done: %s rows, %s failed", leader_rows, failed)

    if not args.skip_appointments:
        appointments = crawl_appointments("national", delay=args.delay, record_state=True)
        appt_payload = appointments_payload(appointments)
        appt_path = dump_json(args.appointments_out, appt_payload)
        events = sum(len(item.get("events", [])) for item in appt_payload) if isinstance(appt_payload, list) else 0
        failed = sum(len(item.get("failed", [])) for item in appt_payload) if isinstance(appt_payload, list) else 0
        new_events = ingest_appointment_results(appt_payload)
        summary["appointments_file"] = str(appt_path)
        summary["appointment_events"] = events
        summary["new_events_ingested"] = new_events
        summary["appointments_failed"] = failed
        logging.info("Appointments done: %s events, %s failed", events, failed)

    summary["persons_in_db"] = len(list_persons())
    summary["db"] = str(Path(args.db).resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
