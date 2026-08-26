# -*- coding: utf-8 -*-
"""Crawl + ingest city registry sites for one or more parent provinces."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import DEFAULT_CITY_REGISTRY, load_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments_site
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import crawl_leaders_site, leaders_payload
from tax_platform.store.ingest import (
    ingest_appointment_results,
    ingest_leader_results,
    known_notice_urls,
)
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", action="append", dest="parents", help="parent_code filter (repeatable)")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--skip-appt", action="store_true")
    parser.add_argument("--skip-leader", action="store_true")
    parser.add_argument("--registry", type=Path, default=DEFAULT_CITY_REGISTRY)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reload_sites()
    rows = load_city_registry(args.registry)
    if args.parents:
        parents = set(args.parents)
        rows = [r for r in rows if r.get("parent_code") in parents]
    codes = [r["code"] for r in rows if r.get("code")]
    logging.info("Crawling %s city sites", len(codes))

    known: set[str] | None = None
    if not args.full:
        conn = connect(args.db)
        try:
            known = known_notice_urls(conn=conn)
        finally:
            conn.close()

    appt_results = []
    lead_results = []
    for code in codes:
        if not args.skip_appt:
            try:
                result = crawl_appointments_site(
                    code,
                    limit=args.limit,
                    delay=args.delay,
                    incremental=not args.full,
                    known_urls=known,
                )
                appt_results.append(result)
                logging.info(
                    "APPT %s list=%s notices=%s events=%s failed=%s skipped=%s",
                    code,
                    result.list_count,
                    len(result.notices),
                    len(result.events),
                    len(result.failed),
                    result.skipped,
                )
            except Exception as exc:  # noqa: BLE001
                logging.exception("APPT FAIL %s: %s", code, exc)
        if not args.skip_leader:
            try:
                result = crawl_leaders_site(code, delay=args.delay)
                lead_results.append(result)
                logging.info("LEAD %s leaders=%s", code, len(result.leaders))
            except Exception as exc:  # noqa: BLE001
                logging.exception("LEAD FAIL %s: %s", code, exc)

    tag = "_".join(args.parents) if args.parents else "all"
    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_results:
        dump_json(f"output/cities_{tag}_appointments.json", appt_payload)
    if lead_results:
        dump_json(f"output/cities_{tag}_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            stats = ingest_appointment_results(appt_payload, conn=conn)
            logging.info("ingest appointments: %s", stats)
        if lead_payload:
            stats = ingest_leader_results(lead_payload, conn=conn)
            logging.info("ingest leaders: %s", stats)
        n = recompute_persons(conn)
        logging.info("recomputed tenures for %s persons", n)
        conn.commit()
    finally:
        conn.close()
    logging.info("done")


if __name__ == "__main__":
    main()
