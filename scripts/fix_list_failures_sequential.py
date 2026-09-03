# -*- coding: utf-8 -*-
"""Process open list-head failures one at a time (see docs/crawl-pitfalls-2026-09-01.md)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.list_heads_buckets import filter_scan_codes, is_deferred_list_head
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.http_client import create_session
from tax_platform.crawler.list_heads_job import scan_bureau_list_heads
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    clear_list_scan_failure,
    connect_list_heads,
    list_open_scan_failures,
)
from tax_platform.store.schema import DEFAULT_DB_PATH, connect

# Stable processing order: small/special first, then by bucket prefix.
# hubei_* intentionally omitted — deferred until their site recovers.
ORDER_PREFIXES = (
    "jilin",
    "shanxi_son_",
    "dejcj",
    "dsijcj",
    "dsjcj",
    "dwjcj",
    "dyjcj",
    "sswfj",
    "swfj",
    "sichuan",
)


def _sort_key(code: str) -> tuple:
    for i, pref in enumerate(ORDER_PREFIXES):
        if code == pref or code.startswith(pref):
            return (i, code)
    return (99, code)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=1, help="How many failures to try this run (default 1)")
    p.add_argument("--code", default="", help="Force one bureau code (bypasses deferred skip)")
    p.add_argument(
        "--include-deferred",
        action="store_true",
        help="Also process deferred buckets (e.g. hubei_hbsw_*)",
    )
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--output", default="output/list_heads_sequential.json")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    reload_sites()
    main_conn = connect(DEFAULT_DB_PATH, light=True)
    heads_conn = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=DEFAULT_DB_PATH)
    fails = list_open_scan_failures(heads_conn)
    codes = sorted([r["bureau_code"] for r in fails], key=_sort_key)
    if args.code:
        codes = [args.code]
        if is_deferred_list_head(args.code) and not args.include_deferred:
            logging.warning("forcing deferred bureau %s via --code", args.code)
    else:
        codes, skipped = filter_scan_codes(codes, include_deferred=args.include_deferred)
        if skipped:
            logging.info("skip deferred: %s", ", ".join(skipped))
        if args.limit > 0:
            codes = codes[: args.limit]

    session = create_session()
    results = []
    try:
        for code in codes:
            logging.info("sequential scan: %s", code)
            r = scan_bureau_list_heads(
                heads_conn, code, main_conn=main_conn, session=session, delay=args.delay
            )
            ok = not r.error and (r.list_count > 0 or r.skipped_no_list)
            if ok and not r.error:
                clear_list_scan_failure(heads_conn, code)
                heads_conn.commit()
            row = {
                "bureau": code,
                "ok": ok,
                "error": r.error,
                "list_count": r.list_count,
                "newest": r.crawled_newest,
                "skipped_no_list": r.skipped_no_list,
            }
            results.append(row)
            logging.info("result %s ok=%s count=%s err=%s", code, ok, r.list_count, r.error)
    finally:
        session.close()
        heads_conn.close()
        main_conn.close()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
