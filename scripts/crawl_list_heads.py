# -*- coding: utf-8 -*-
"""扫各局人事任免列表页；攒当周新公告，全部扫完后再整体入库。

扫列表用独立库 ``output/list_heads.db``（appointment_list_heads / updates），
避免长时间锁主库 ``tax_hr.db``（账号 / notices / 变动动态）。

流程::

    1. 扫完全部有列表的税务局，新公告先写入 list_heads.db
    2. 抓取失败的站点打标记，全量结束后自动复跑一轮
    3. 全部扫完后一次性详情爬 + ingest → 主库 notices / appointment_events
    4. updates 行标记 ingested_at

示例::

    python scripts/crawl_list_heads.py
    python scripts/crawl_list_heads.py --site shanghai --level district
    python scripts/crawl_list_heads.py --skip-aggregate
    python scripts/crawl_list_heads.py --no-retry
"""

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
from tax_platform.config.sites import ALL_SITES, get_site
from tax_platform.crawler.http_client import create_session
from tax_platform.crawler.job_io import dump_json, resolve_site_codes
from tax_platform.crawler.list_heads_job import (
    _log_scan_result,
    aggregate_tagged_into_notices,
    retry_failed_list_heads,
    scan_bureau_list_heads,
)
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    connect_list_heads,
    list_open_scan_failures,
    list_waiting_updates,
)
from tax_platform.store.schema import connect


def _resolve_codes(site: str, level: str | None, max_sites: int) -> list[str]:
    if site in {"all", "national", "cities"}:
        codes = resolve_site_codes(site, level=level)
    elif level:
        get_site(site)
        codes = [
            s.code
            for s in ALL_SITES
            if s.parent_code == site
            and s.level == level
            and (s.appointment_list_url or "").strip()
        ]
        if not codes:
            codes = resolve_site_codes(site)
    else:
        codes = resolve_site_codes(site)
    if max_sites > 0:
        codes = codes[:max_sites]
    return codes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db", help="主库（账号/notices）")
    parser.add_argument(
        "--heads-db",
        default=str(DEFAULT_LIST_HEADS_DB_PATH),
        help="列表监控独立库（默认 output/list_heads.db）",
    )
    parser.add_argument("--site", default="all", help="Bureau code or all")
    parser.add_argument(
        "--level",
        default=None,
        help="Optional: headquarters / province / city / district",
    )
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=None,
        help="Delay for failure retry round (default: max(delay, 0.8))",
    )
    parser.add_argument(
        "--max-sites",
        type=int,
        default=0,
        help="Only first N sites (0 = no cap); for smoke tests",
    )
    parser.add_argument(
        "--skip-aggregate",
        action="store_true",
        help="Only refresh list-heads table; do not detail-crawl / ingest",
    )
    parser.add_argument(
        "--aggregate-only",
        action="store_true",
        help="Skip list scan; only ingest already-tagged heads",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Do not re-scan failed list pages after the first pass",
    )
    parser.add_argument(
        "--retry-only",
        action="store_true",
        help="Skip full scan; only retry open failures in list_heads.db",
    )
    parser.add_argument(
        "--include-deferred",
        action="store_true",
        help="Include deferred buckets (hubei_hbsw_*, province jilin); default skips per crawl-pitfalls doc",
    )
    parser.add_argument(
        "--output",
        default="output/list_heads_run.json",
        help="Write run summary JSON",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    main_conn = connect(args.db)
    heads_conn = connect_list_heads(args.heads_db, main_db_path=args.db)
    logging.info("list-heads DB: %s", Path(args.heads_db).resolve())

    scan_results = []
    first_pass_errors: list[dict[str, str]] = []
    retry_meta: dict | None = None

    if args.retry_only:
        open_fails = list_open_scan_failures(heads_conn)
        codes = [r["bureau_code"] for r in open_fails]
        codes, deferred = filter_scan_codes(codes, include_deferred=args.include_deferred)
        if deferred:
            logging.info("retry-only: skipping %s deferred sites", len(deferred))
        logging.info("retry-only: %s open failures", len(codes))
        session = create_session()
        try:
            scan_results = retry_failed_list_heads(
                heads_conn,
                [],
                main_conn=main_conn,
                session=session,
                delay=args.retry_delay if args.retry_delay is not None else max(args.delay, 0.8),
                codes=codes,
            )
        finally:
            session.close()
        first_pass_errors = [
            {"bureau": r["bureau_code"], "error": r.get("error") or ""} for r in open_fails
        ]
        retry_meta = {
            "attempted": len(codes),
            "recovered": sum(1 for r in scan_results if not r.error),
            "still_failing": sum(1 for r in scan_results if r.error),
        }
    elif not args.aggregate_only:
        codes = _resolve_codes(args.site, args.level, args.max_sites)
        codes, deferred = filter_scan_codes(codes, include_deferred=args.include_deferred)
        if deferred:
            logging.info(
                "skipping %s deferred bucket sites (use --include-deferred): %s",
                len(deferred),
                ", ".join(deferred[:8]) + ("..." if len(deferred) > 8 else ""),
            )
        logging.info("scanning %s sites", len(codes))
        session = create_session()
        try:
            for i, code in enumerate(codes, start=1):
                result = scan_bureau_list_heads(
                    heads_conn,
                    code,
                    main_conn=main_conn,
                    session=session,
                    delay=args.delay,
                )
                scan_results.append(result)
                _log_scan_result(result)
                heads_conn.commit()
                if i % 20 == 0:
                    logging.info("list-heads progress %s/%s", i, len(codes))

            first_pass_errors = [
                {"bureau": r.bureau, "error": r.error or ""}
                for r in scan_results
                if r.error
            ]
            if not args.no_retry and first_pass_errors:
                before = {r.bureau for r in scan_results if r.error and not is_deferred_list_head(r.bureau)}
                retry_codes = [c for c in before if not is_deferred_list_head(c)]
                scan_results = retry_failed_list_heads(
                    heads_conn,
                    scan_results,
                    main_conn=main_conn,
                    session=session,
                    delay=(
                        args.retry_delay
                        if args.retry_delay is not None
                        else max(args.delay, 0.8)
                    ),
                    codes=retry_codes if retry_codes else None,
                )
                after = {r.bureau for r in scan_results if r.error}
                retry_meta = {
                    "attempted": len(before),
                    "recovered": len(before - after),
                    "still_failing": len(after),
                    "still_failing_bureaus": sorted(after),
                }
        finally:
            session.close()
        heads_conn.commit()

    updated = [r for r in scan_results if r.updated]
    seeded = [r for r in scan_results if r.seeded]
    errors = [r for r in scan_results if r.error]
    logging.info(
        "scan done: %s sites, %s updated, %s seeded, %s errors (first-pass %s)",
        len(scan_results),
        len(updated),
        len(seeded),
        len(errors),
        len(first_pass_errors),
    )
    if retry_meta:
        logging.info(
            "retry: attempted=%s recovered=%s still_failing=%s",
            retry_meta.get("attempted"),
            retry_meta.get("recovered"),
            retry_meta.get("still_failing"),
        )

    aggregate = None
    if not args.skip_aggregate and not args.retry_only:
        aggregate = aggregate_tagged_into_notices(
            heads_conn, main_conn, delay=args.delay
        )
        logging.info(
            "aggregate: %s bureaus, %s waiting updates, %s events ingested",
            aggregate.get("updated_bureaus"),
            aggregate.get("pending_notices"),
            aggregate.get("ingested_events"),
        )
    else:
        logging.info(
            "waiting updates left in heads DB: %s",
            len(list_waiting_updates(heads_conn)),
        )

    open_failures = list_open_scan_failures(heads_conn)
    payload = {
        "heads_db": str(Path(args.heads_db).resolve()),
        "scan": {
            "sites": len(scan_results),
            "updated": len(updated),
            "seeded": len(seeded),
            "first_pass_errors": first_pass_errors,
            "retry": retry_meta,
            "errors": [{"bureau": r.bureau, "error": r.error} for r in errors],
            "open_failures": open_failures,
            "updated_bureaus": [
                {
                    "bureau": r.bureau,
                    "baseline": r.baseline_newest,
                    "newest": r.crawled_newest,
                    "new_count": len(r.new_items),
                    "new_items": [
                        {
                            "title": h.title,
                            "source_url": h.source_url,
                            "published_on": h.published_on,
                        }
                        for h in r.new_items
                    ],
                    "heads": [
                        {
                            "rank": h.rank,
                            "title": h.title,
                            "source_url": h.source_url,
                            "published_on": h.published_on,
                        }
                        for h in r.heads
                    ],
                }
                for r in updated
            ],
        },
        "aggregate": {
            "updated_bureaus": (aggregate or {}).get("updated_bureaus"),
            "pending_notices": (aggregate or {}).get("pending_notices"),
            "ingested_events": (aggregate or {}).get("ingested_events"),
            "tagged": (aggregate or {}).get("tagged"),
        }
        if aggregate is not None
        else None,
    }
    out = dump_json(args.output, payload)
    logging.info("Wrote %s", out)
    print(
        json.dumps(
            {
                "sites": payload["scan"]["sites"],
                "updated": payload["scan"]["updated"],
                "seeded": payload["scan"]["seeded"],
                "first_pass_errors": len(first_pass_errors),
                "retry": retry_meta,
                "final_errors": len(errors),
                "open_failures": len(open_failures),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    heads_conn.close()
    main_conn.close()


if __name__ == "__main__":
    main()
