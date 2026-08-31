# -*- coding: utf-8 -*-
"""Crawl Chongqing district leader pages that are still empty in DB."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import crawl_leaders_site, leaders_payload
from tax_platform.store.ingest import ingest_leader_results
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

WARMUP = (
    "https://chongqing.chinatax.gov.cn/qxtax/",
    "https://chongqing.chinatax.gov.cn/qxtax/yz/ldjj/",
)


def _leader_count(db: str, code: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM leader_duties WHERE bureau_code=?", (code,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--min-leaders", type=int, default=3)
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--force", action="store_true", help="Crawl even if leaders already present")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reload_sites()
    rows = [r for r in load_city_registry() if str(r.get("parent_code")) == "chongqing"]
    pending: list[dict] = []
    for row in rows:
        code = row.get("code") or ""
        if args.codes and code not in args.codes:
            continue
        lead_url = row.get("leader_intro_url") or ""
        n = _leader_count(args.db, code)
        if not args.force and n >= args.min_leaders:
            logging.info("SKIP %s leaders=%s", code, n)
            continue
        if not lead_url:
            logging.warning("NO_URL %s %s", code, row.get("name"))
            continue
        pending.append(row)

    logging.info("Chongqing missing-leader crawl: %s sites", len(pending))
    if not pending:
        return 0

    session = create_session()
    for url in WARMUP:
        try:
            fetch_html(session, url, allow_browser=True, follow_meta_refresh=False)
        except Exception as exc:  # noqa: BLE001
            logging.debug("warmup %s: %s", url, exc)
        time.sleep(0.3)

    report: dict = {"cities": {}, "failed": [], "no_url": []}
    for i, row in enumerate(pending, 1):
        code = row["code"]
        logging.info("[%s/%s] === %s %s ===", i, len(pending), code, row.get("name"))
        try:
            lr = crawl_leaders_site(code, delay=args.delay, session=session)
            junk = {"市局频道", "工作动态", "主要职责", "友情链接", "局长信箱", "负责党委"}
            lr.leaders = [d for d in lr.leaders if d.person_name not in junk]
            logging.info("LEAD %s n=%s failed=%s", code, len(lr.leaders), len(lr.failed))
            conn = connect(args.db)
            try:
                if lr.leaders:
                    ingest_leader_results(leaders_payload([lr]), conn=conn)
                    n = recompute_persons(conn)
                    conn.commit()
                    logging.info("commit %s persons=%s", code, n)
            finally:
                conn.close()
            report["cities"][code] = {
                "leaders": len(lr.leaders),
                "failed": len(lr.failed),
            }
        except Exception as exc:  # noqa: BLE001
            logging.exception("FAIL %s: %s", code, exc)
            report["failed"].append({"code": code, "error": str(exc)})
        time.sleep(args.delay)

    session.close()
    dump_json("output/chongqing_missing_leaders_report.json", report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
