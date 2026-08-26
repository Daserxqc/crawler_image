"""Discover city/district sites under a province and write city_sites_registry.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_discovery import (
    candidates_to_entries,
    discover_city_sites_from_html,
    discover_province_children,
)
from tax_platform.config.city_sites_io import DEFAULT_CITY_REGISTRY, merge_city_entries
from tax_platform.config.sites import get_site
from tax_platform.config.sites_shanghai import SHANGHAI_SITES


def _validate_shanghai_offline() -> dict:
    """Sanity-check extractor against the known Shanghai district path pattern."""
    html = """
    <html><body>
      <a href="/pdtax/xxgk/rsrm/">浦东新区税务局人事任免</a>
      <a href="/pdtax/xxgk/ldjj/">浦东新区税务局领导简介</a>
      <a href="/hptax/xxgk/rsrm/">黄浦区税务局人事任免</a>
      <a href="/xhtax/xxgk/ldjj/">徐汇区税务局领导介绍</a>
    </body></html>
    """
    found = discover_city_sites_from_html(
        html,
        "https://shanghai.chinatax.gov.cn/xxgk/",
        parent_code="shanghai",
        region="上海市",
    )
    codes = {c.code for c in found}
    expected = {"pdtax", "hptax", "xhtax"}
    return {
        "ok": expected.issubset(codes),
        "found": sorted(codes),
        "expected_subset": sorted(expected),
        "configured_shanghai_districts": len([s for s in SHANGHAI_SITES if s.level == "district"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parent",
        default="shanghai",
        help="Province bureau code to scan (default: shanghai)",
    )
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output", type=Path, default=DEFAULT_CITY_REGISTRY)
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Only run the Shanghai HTML fixture check (no network)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without writing the registry",
    )
    args = parser.parse_args()

    if args.offline_check:
        print(json.dumps(_validate_shanghai_offline(), ensure_ascii=False, indent=2))
        return

    parent = get_site(args.parent)
    candidates = discover_province_children(args.parent, delay=args.delay)
    entries = candidates_to_entries(candidates)
    payload = {
        "parent": args.parent,
        "parent_name": parent.name,
        "count": len(entries),
        "entries": entries,
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    merge_city_entries(entries, path=args.output)
    print(
        json.dumps(
            {"wrote": str(args.output.resolve()), "parent": args.parent, "count": len(entries)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
