# -*- coding: utf-8 -*-
"""3-hour sprint: parallel discover all province city hubs, then crawl+ingest."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.manual_skip import filter_parents, should_skip_auto_crawl
from tax_platform.config.city_discovery import candidates_to_entries, discover_province_children
from tax_platform.config.city_sites_io import DEFAULT_CITY_REGISTRY, load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.config.sites_provinces import PROVINCE_SITES
from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments_site
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import crawl_leaders_site, leaders_payload
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons


def _discover_one(
    parent_code: str,
    *,
    delay: float,
    deepen_workers: int,
) -> tuple[str, list[dict], str | None]:
    try:
        candidates = discover_province_children(
            parent_code,
            delay=delay,
            deepen=True,
            deepen_workers=deepen_workers,
        )
        entries = candidates_to_entries(candidates)
        return parent_code, entries, None
    except Exception as exc:  # noqa: BLE001
        return parent_code, [], str(exc)


def _crawl_one(
    code: str,
    *,
    delay: float,
    detail_workers: int,
    known: set[str] | None,
    full: bool,
    skip_appt: bool,
    skip_leader: bool,
) -> tuple[object | None, object | None, str | None]:
    appt = lead = None
    try:
        if not skip_appt:
            appt = crawl_appointments_site(
                code,
                limit=0,
                delay=delay,
                incremental=not full,
                known_urls=known,
                detail_workers=detail_workers,
            )
        if not skip_leader:
            lead = crawl_leaders_site(code, delay=delay)
        return appt, lead, None
    except Exception as exc:  # noqa: BLE001
        return appt, lead, f"{code}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.05)
    parser.add_argument("--discover-workers", type=int, default=4)
    parser.add_argument("--deepen-workers", type=int, default=6)
    parser.add_argument("--crawl-workers", type=int, default=4)
    parser.add_argument("--detail-workers", type=int, default=8)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--registry", type=Path, default=DEFAULT_CITY_REGISTRY)
    parser.add_argument("--skip-discover", action="store_true")
    parser.add_argument("--skip-crawl", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    t0 = time.time()
    parents = [s.code for s in PROVINCE_SITES]
    parents, skipped_parents = filter_parents(parents)
    if skipped_parents:
        logging.info("SKIP manual provinces: %s", ", ".join(skipped_parents))
    all_entries: list[dict] = []

    if not args.skip_discover:
        logging.info("DISCOVER %s provinces (workers=%s)", len(parents), args.discover_workers)
        discovered: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=args.discover_workers) as pool:
            futs = {
                pool.submit(
                    _discover_one,
                    code,
                    delay=args.delay,
                    deepen_workers=args.deepen_workers,
                ): code
                for code in parents
            }
            for fut in as_completed(futs):
                parent_code, entries, err = fut.result()
                discovered[parent_code] = entries
                appt_n = sum(1 for e in entries if e.get("appointment_list_url"))
                lead_n = sum(1 for e in entries if e.get("leader_intro_url"))
                if err:
                    logging.warning("DISCOVER FAIL %s: %s", parent_code, err)
                else:
                    logging.info(
                        "DISCOVER %s cities=%s appt=%s leader=%s",
                        parent_code,
                        len(entries),
                        appt_n,
                        lead_n,
                    )

        for code in parents:
            all_entries.extend(discovered.get(code, []))
        kept = [e for e in load_city_registry(args.registry) if e.get("parent_code") not in set(parents)]
        save_city_registry(kept + all_entries, path=args.registry)
        reload_sites()
        logging.info(
            "REGISTRY wrote %s cities (%.0fs)",
            len(load_city_registry(args.registry)),
            time.time() - t0,
        )

    if args.skip_crawl:
        return

    reload_sites()
    rows = load_city_registry(args.registry)
    codes = [r["code"] for r in rows if r.get("code")]
    logging.info("CRAWL %s city sites (workers=%s detail=%s)", len(codes), args.crawl_workers, args.detail_workers)

    known: set[str] | None = None
    if not args.full:
        conn = connect(args.db)
        try:
            known = known_notice_urls(conn=conn)
        finally:
            conn.close()

    appt_results = []
    lead_results = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=args.crawl_workers) as pool:
        futs = {
            pool.submit(
                _crawl_one,
                code,
                delay=args.delay,
                detail_workers=args.detail_workers,
                known=known,
                full=args.full,
                skip_appt=False,
                skip_leader=False,
            ): code
            for code in codes
        }
        for fut in as_completed(futs):
            code = futs[fut]
            appt, lead, err = fut.result()
            if appt is not None:
                appt_results.append(appt)
                logging.info(
                    "APPT %s list=%s notices=%s events=%s skip=%s fail=%s",
                    code,
                    appt.list_count,
                    len(appt.notices),
                    len(appt.events),
                    appt.skipped,
                    len(appt.failed),
                )
            if lead is not None:
                lead_results.append(lead)
                logging.info("LEAD %s leaders=%s", code, len(lead.leaders))
            if err:
                errors.append(err)
                logging.error("CRAWL ERR %s", err)

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/cities_sprint_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/cities_sprint_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
        if lead_payload:
            logging.info("ingest lead %s", ingest_leader_results(lead_payload, conn=conn))
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
    finally:
        conn.close()

    summary = {
        "elapsed_sec": round(time.time() - t0),
        "registry_cities": len(codes),
        "crawled_appt": len(appt_results),
        "crawled_lead": len(lead_results),
        "errors": errors[:20],
    }
    Path("output/city_sprint_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logging.info("SPRINT DONE %s", summary)


if __name__ == "__main__":
    main()
