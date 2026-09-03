# -*- coding: utf-8 -*-
"""Rediscover appointment list URLs from stored sources only (no blind web guessing).

Workflow (see docs/crawl-pitfalls-2026-09-01.md):
  1. Collect candidates: registry → org_units → ingest-script fallbacks → henan_path pattern
  2. Validate each with a quick fetch + list parse
  3. On success: patch city_sites_registry.json and/or sites_provinces override
  4. If no candidate or all fail: print NEEDS_USER and stop (do not guess)

Examples::

    python scripts/rediscover_list_urls.py henan guangdong_shenzhen
    python scripts/rediscover_list_urls.py --from-failures
    python scripts/rediscover_list_urls.py --apply henan guangdong_shenzhen
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

from tax_platform.config.city_sites_io import load_city_registry, merge_city_entries, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.config.sites_provinces import PROVINCE_URL_OVERRIDES
from tax_platform.config.url_rediscovery import rediscover_bureau_url
from tax_platform.store.list_heads import connect_list_heads, DEFAULT_LIST_HEADS_DB_PATH, list_open_scan_failures
from tax_platform.store.schema import connect, DEFAULT_DB_PATH

PROVINCE_CODES = frozenset(PROVINCE_URL_OVERRIDES.keys()) | frozenset(
    {
        "henan",
        "hebei",
        "jilin",
        "sichuan",
        "shanxi",
        "hubei",
        "guangdong",
        "shanghai",
    }
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("codes", nargs="*", help="Bureau codes to rediscover")
    p.add_argument(
        "--from-failures",
        action="store_true",
        help="Use open list_heads scan failures as code list",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write validated URL back to registry (city) or print province override hint",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH))
    p.add_argument("--output", default="output/url_rediscovery.json")
    return p.parse_args()


def _apply_province_hint(code: str, url: str) -> None:
    logging.info(
        "Province %s: add to PROVINCE_URL_OVERRIDES in sites_provinces.py:\n"
        '    "%s": {"appointment_list_url": "%s"},',
        code,
        code,
        url,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    codes = list(args.codes)
    if args.from_failures:
        heads = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=args.db)
        try:
            codes = [r["bureau_code"] for r in list_open_scan_failures(heads)]
        finally:
            heads.close()
    if not codes:
        logging.error("No bureau codes; pass codes or --from-failures")
        sys.exit(1)

    results = []
    needs_user: list[str] = []
    for code in codes:
        r = rediscover_bureau_url(code, db_path=args.db)
        row = {
            "bureau": code,
            "validated_url": r.validated_url,
            "items": r.validated_items,
            "needs_user": r.needs_user,
            "candidates": [{"url": c.url, "source": c.source} for c in r.candidates],
            "notes": r.notes,
        }
        results.append(row)
        if r.needs_user:
            needs_user.append(code)
            logging.warning("%s NEEDS_USER — %s", code, "; ".join(r.notes))
        elif r.validated_url:
            logging.info("%s OK → %s (%s items)", code, r.validated_url, r.validated_items)
            if args.apply:
                if code in PROVINCE_CODES or not code.startswith(
                    ("henan_path_", "hebei_", "jilin_", "sichuan_", "shanxi_", "shanghai_")
                ) and "_" not in code:
                    _apply_province_hint(code, r.validated_url)
                else:
                    merge_city_entries(
                        [
                            {
                                "code": code,
                                "appointment_list_url": r.validated_url,
                                "notes": ["url_rediscovery"],
                            }
                        ]
                    )
                    logging.info("patched registry for %s", code)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    reload_sites()

    if needs_user:
        print("\n=== NEEDS_USER (请提供网页截图/链接) ===")
        for c in needs_user:
            print(" ", c)
        sys.exit(2)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
