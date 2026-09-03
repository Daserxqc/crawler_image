# -*- coding: utf-8 -*-
"""Scan city sites that have appointment_list_url but no list heads yet."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.list_heads_buckets import is_deferred_list_head, is_no_appt_site
from tax_platform.config.sites import list_sites, reload_sites
from tax_platform.crawler.http_client import create_session
from tax_platform.crawler.list_heads_job import scan_bureau_list_heads
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    clear_list_scan_failure,
    connect_list_heads,
    load_heads,
    list_open_scan_failures,
)
from tax_platform.store.schema import DEFAULT_DB_PATH, connect


def missing_city_codes(*, prefix: str = "") -> list[str]:
    reload_sites()
    heads = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=DEFAULT_DB_PATH)
    try:
        out: list[str] = []
        for s in list_sites(level="city"):
            if prefix and not s.code.startswith(prefix):
                continue
            if is_no_appt_site(s.code) or is_deferred_list_head(s.code):
                continue
            if not (s.appointment_list_url or "").strip():
                continue
            if load_heads(heads, s.code):
                continue
            out.append(s.code)
        return sorted(out)
    finally:
        heads.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix", default="", help="Only codes with this prefix")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--delay", type=float, default=0.2)
    p.add_argument("--output", default="output/list_heads_missing_cities.json")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    codes = missing_city_codes(prefix=args.prefix)
    if args.limit > 0:
        codes = codes[: args.limit]
    logging.info("scanning %s missing-head cities", len(codes))

    main_conn = connect(DEFAULT_DB_PATH, light=True)
    heads_conn = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=DEFAULT_DB_PATH)
    session = create_session()
    results = []
    by_parent: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "fail": 0, "empty": 0})
    try:
        for i, code in enumerate(codes, 1):
            logging.info("[%s/%s] %s", i, len(codes), code)
            r = scan_bureau_list_heads(
                heads_conn, code, main_conn=main_conn, session=session, delay=args.delay
            )
            ok = not r.error and r.list_count > 0
            empty = not r.error and r.list_count == 0
            if ok:
                clear_list_scan_failure(heads_conn, code)
                heads_conn.commit()
                bucket = "ok"
            elif empty:
                bucket = "empty"
            else:
                bucket = "fail"
            parent = code.split("_")[0]
            by_parent[parent][bucket if bucket != "empty" else "empty"] += 1
            if bucket == "empty":
                by_parent[parent]["empty"] += 0  # already counted
            row = {
                "bureau": code,
                "ok": ok,
                "empty": empty,
                "error": (r.error or "")[:240] or None,
                "list_count": r.list_count,
                "newest": r.crawled_newest,
            }
            results.append(row)
            logging.info(
                "  -> ok=%s empty=%s count=%s err=%s",
                ok,
                empty,
                r.list_count,
                (r.error or "")[:120],
            )
    finally:
        session.close()
        heads_conn.close()
        main_conn.close()

    summary = {
        "attempted": len(results),
        "ok": sum(1 for r in results if r["ok"]),
        "empty": sum(1 for r in results if r["empty"]),
        "fail": sum(1 for r in results if not r["ok"] and not r["empty"]),
        "by_parent": dict(by_parent),
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("attempted", "ok", "empty", "fail", "by_parent")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
