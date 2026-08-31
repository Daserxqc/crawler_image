# -*- coding: utf-8 -*-
"""Re-discover Shaanxi URLs, clear contaminated cities, re-crawl them."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

# Wrong leaders currently (汉中串到安康/宝鸡；汉中 URL 也可能被污染)
CONTAMINATED = [
    "shaanxi_col2653",  # 安康
    "shaanxi_col399",  # 宝鸡
    "shaanxi_col4544",  # 汉中
]
JUNK = {"市局频道", "工作动态", "主要职责", "友情链接"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--skip-discover", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.skip_discover:
        logging.info("discover shaanxi…")
        rc = subprocess.call(
            [
                sys.executable,
                str(ROOT / "scripts" / "discover_city_sites.py"),
                "--parent",
                "shaanxi",
                "--delay",
                str(args.delay),
            ],
            cwd=str(ROOT),
        )
        logging.info("discover exit %s", rc)

    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    for code in CONTAMINATED:
        conn.execute("DELETE FROM leader_duties WHERE bureau_code=?", (code,))
        conn.execute("DELETE FROM appointment_events WHERE bureau_code=?", (code,))
        logging.info("cleared %s", code)
    conn.commit()
    conn.close()

    report: dict = {"cities": {}, "failed": []}
    for code in CONTAMINATED:
        logging.info("=== recrawl %s ===", code)
        lr = ar = None
        try:
            lr = crawl_leaders_site(code, delay=args.delay)
            lr.leaders = [d for d in lr.leaders if d.person_name not in JUNK]
            logging.info("LEAD %s n=%s", code, len(lr.leaders))
        except Exception as exc:  # noqa: BLE001
            logging.exception("LEAD %s", code)
            report["failed"].append({"code": code, "kind": "lead", "error": str(exc)})
        try:
            ar = crawl_appointments_site(code, delay=args.delay, known_urls=known)
            logging.info("APPT %s list=%s events=%s", code, ar.list_count, len(ar.events))
        except Exception as exc:  # noqa: BLE001
            logging.exception("APPT %s", code)
            report["failed"].append({"code": code, "kind": "appt", "error": str(exc)})

        conn = connect(args.db)
        try:
            if ar and (ar.list_count or ar.notices):
                ingest_appointment_results(appointments_payload([ar]), conn=conn)
            if lr and lr.leaders:
                ingest_leader_results(leaders_payload([lr]), conn=conn)
            recompute_persons(conn)
            conn.commit()
        finally:
            conn.close()
        report["cities"][code] = {
            "leaders": len(lr.leaders) if lr else 0,
            "list": ar.list_count if ar else 0,
            "events": len(ar.events) if ar else 0,
            "names": [d.person_name for d in (lr.leaders if lr else [])],
        }

    dump_json("output/shaanxi_recrawl_report.json", report)
    c = sqlite3.connect(args.db)
    for code in CONTAMINATED:
        names = [
            r[0]
            for r in c.execute(
                "select person_name from leader_duties where bureau_code=? order by 1",
                (code,),
            )
        ]
        logging.info("FINAL %s %s", code, names)
    c.close()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
