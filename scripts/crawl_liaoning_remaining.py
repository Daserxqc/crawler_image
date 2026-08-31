# -*- coding: utf-8 -*-
"""Crawl remaining Liaoning real city bureaus (empty after Shenyang/Dalian)."""

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

from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments_site
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import crawl_leaders_site, leaders_payload
from tax_platform.store.ingest import (
    ingest_appointment_results,
    ingest_leader_results,
    known_notice_urls,
)
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

# Real municipal hubs still empty / appt-missing (沈阳+大连已齐)
REMAINING = [
    "liaoning_col320",  # 鞍山
    "liaoning_col382",  # 抚顺
    "liaoning_col455",  # 本溪
    "liaoning_col492",  # 丹东
    "liaoning_col529",  # 锦州
    "liaoning_col566",  # 营口
    "liaoning_col603",  # 阜新
    "liaoning_col640",  # 辽阳
    "liaoning_col677",  # 铁岭
    "liaoning_col714",  # 朝阳
    "liaoning_col751",  # 盘锦
    "liaoning_col788",  # 葫芦岛 (lead ok, need appt)
]

WARMUP_URLS = (
    "http://liaoning.chinatax.gov.cn/col/col313/index.html",  # 沈阳 hub
    "http://liaoning.chinatax.gov.cn/col/col873/index.html",  # 沈阳 leader
)


def _warmup_liaoning(session) -> None:
    for url in WARMUP_URLS:
        try:
            fetch_html(session, url, allow_browser=True, follow_meta_refresh=False)
        except Exception as exc:  # noqa: BLE001
            logging.debug("warmup skip %s: %s", url, exc)
        time.sleep(0.4)


def _city_counts(db: str, code: str) -> tuple[int, int]:
    conn = sqlite3.connect(db)
    try:
        leaders = conn.execute(
            "SELECT COUNT(*) FROM leader_duties WHERE bureau_code=?", (code,)
        ).fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM appointment_events WHERE bureau_code=?", (code,)
        ).fetchone()[0]
        return int(leaders), int(events)
    finally:
        conn.close()


def _needs_crawl(code: str, *, leaders: int, events: int, leaders_only: bool, appt_only: bool) -> bool:
    if leaders_only:
        return leaders < 3
    if appt_only:
        return events < 10
    return leaders < 3 or events < 10


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument(
        "--skip-done",
        action="store_true",
        help="Skip cities that already have >=3 leaders and >=10 appointment events",
    )
    parser.add_argument("--leaders-only", action="store_true")
    parser.add_argument("--appt-only", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reload_sites()
    codes = args.codes or REMAINING

    pending: list[str] = []
    for code in codes:
        leaders, events = _city_counts(args.db, code)
        if args.skip_done and not _needs_crawl(
            code,
            leaders=leaders,
            events=events,
            leaders_only=args.leaders_only,
            appt_only=args.appt_only,
        ):
            logging.info("SKIP %s (leaders=%s events=%s)", code, leaders, events)
            continue
        pending.append(code)

    logging.info("Liaoning remaining crawl: %s sites (of %s requested)", len(pending), len(codes))
    if not pending:
        print({"cities": {}, "failed": [], "skipped": codes})
        return 0

    session = create_session()
    _warmup_liaoning(session)

    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    report: dict = {"cities": {}, "failed": [], "skipped": [c for c in codes if c not in pending]}
    for i, code in enumerate(pending, 1):
        if i > 1 and (i - 1) % 3 == 0:
            _warmup_liaoning(session)
        logging.info("[%s/%s] === %s ===", i, len(pending), code)
        lr = ar = None
        if not args.appt_only:
            try:
                lr = crawl_leaders_site(code, delay=args.delay, session=session)
                logging.info("LEAD %s n=%s failed=%s", code, len(lr.leaders), len(lr.failed))
            except Exception as exc:  # noqa: BLE001
                logging.exception("LEAD fail %s: %s", code, exc)
                report["failed"].append({"code": code, "kind": "leader", "error": str(exc)})
        if not args.leaders_only:
            try:
                ar = crawl_appointments_site(
                    code,
                    delay=args.delay,
                    known_urls=known,
                    session=session,
                )
                logging.info(
                    "APPT %s list=%s notices=%s events=%s failed=%s",
                    code,
                    ar.list_count,
                    len(ar.notices),
                    len(ar.events),
                    len(ar.failed),
                )
            except Exception as exc:  # noqa: BLE001
                logging.exception("APPT fail %s: %s", code, exc)
                report["failed"].append({"code": code, "kind": "appt", "error": str(exc)})

        conn = connect(args.db)
        try:
            if ar is not None and (ar.list_count or ar.notices):
                ingest_appointment_results(appointments_payload([ar]), conn=conn)
                for n in ar.notices:
                    if n.source_url:
                        known.add(n.source_url)
            if lr is not None and lr.leaders:
                junk = {"市局频道", "工作动态", "主要职责", "友情链接"}
                lr.leaders = [d for d in lr.leaders if d.person_name not in junk]
                if lr.leaders:
                    ingest_leader_results(leaders_payload([lr]), conn=conn)
            n = recompute_persons(conn)
            conn.commit()
            logging.info("commit %s persons=%s", code, n)
        finally:
            conn.close()

        report["cities"][code] = {
            "leaders": len(lr.leaders) if lr else 0,
            "appt_list": ar.list_count if ar else 0,
            "appt_events": len(ar.events) if ar else 0,
            "lead_failed": len(lr.failed) if lr else 0,
            "appt_failed": len(ar.failed) if ar else 0,
        }
        time.sleep(args.delay)

    session.close()
    dump_json("output/liaoning_remaining_report.json", report)
    c = sqlite3.connect(args.db)
    for code in pending:
        l = c.execute("select count(*) from leader_duties where bureau_code=?", (code,)).fetchone()[0]
        e = c.execute(
            "select count(*) from appointment_events where bureau_code=?", (code,)
        ).fetchone()[0]
        logging.info("DB %s leaders=%s events=%s", code, l, e)
    c.close()
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
