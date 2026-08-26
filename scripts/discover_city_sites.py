"""Discover city/district sites under province(s) and write city_sites_registry.json."""

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
    discover_city_channel_hubs,
    discover_city_sites_from_html,
    discover_province_children,
)
from tax_platform.config.city_sites_io import (
    DEFAULT_CITY_REGISTRY,
    load_city_registry,
    merge_city_entries,
    save_city_registry,
)
from tax_platform.config.sites import get_site, reload_sites
from tax_platform.config.sites_shanghai import SHANGHAI_SITES


def _validate_offline() -> dict:
    """Sanity-check extractors against Shanghai + 市局频道 fixtures."""
    sh_html = """
    <html><body>
      <a href="/pdtax/xxgk/rsrm/">浦东新区税务局人事任免</a>
      <a href="/pdtax/xxgk/ldjj/">浦东新区税务局领导简介</a>
      <a href="/hptax/xxgk/rsrm/">黄浦区税务局人事任免</a>
      <a href="/xhtax/xxgk/ldjj/">徐汇区税务局领导介绍</a>
    </body></html>
    """
    found = discover_city_sites_from_html(
        sh_html,
        "https://shanghai.chinatax.gov.cn/xxgk/",
        parent_code="shanghai",
        region="上海市",
    )
    codes = {c.code for c in found}
    expected = {"pdtax", "hptax", "xhtax"}

    sd_html = """
    <div class="footer">
      <span>市局频道</span>
      <ul>
        <li><a href="/col/col40/index.html" target="_blank">济南</a></li>
        <li><a href="/col/col41/index.html" target="_blank">淄博</a></li>
        <li><a href="/col/col54/index.html" target="_blank">菏泽</a></li>
      </ul>
    </div>
    """
    hubs = discover_city_channel_hubs(
        sd_html,
        "https://shandong.chinatax.gov.cn/",
        parent_code="shandong",
        region="山东省",
    )
    hub_codes = {c.code for c in hubs}
    return {
        "shanghai_ok": expected.issubset(codes),
        "shanghai_found": sorted(codes),
        "channel_ok": {"shandong_col40", "shandong_col41", "shandong_col54"}.issubset(hub_codes),
        "channel_found": sorted(hub_codes),
        "configured_shanghai_districts": len([s for s in SHANGHAI_SITES if s.level == "district"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parent",
        action="append",
        dest="parents",
        help="Province bureau code to scan (repeatable). Default: shanghai",
    )
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--output", type=Path, default=DEFAULT_CITY_REGISTRY)
    parser.add_argument(
        "--offline-check",
        action="store_true",
        help="Only run HTML fixture checks (no network)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidates without writing the registry",
    )
    parser.add_argument(
        "--all-provinces",
        action="store_true",
        help="Discover city hubs under every province site in the registry",
    )
    parser.add_argument(
        "--no-deepen",
        action="store_true",
        help="Only parse province pages; do not fetch each city hub",
    )
    args = parser.parse_args()

    if args.offline_check:
        print(json.dumps(_validate_offline(), ensure_ascii=False, indent=2))
        return

    if args.all_provinces:
        from tax_platform.config.sites_provinces import PROVINCE_SITES

        parents = [s.code for s in PROVINCE_SITES]
    else:
        parents = args.parents or ["shanghai"]
    all_entries: list[dict] = []
    summary = []
    for parent_code in parents:
        print(f"[discover] start {parent_code}", flush=True)
        parent = get_site(parent_code)
        try:
            candidates = discover_province_children(
                parent_code,
                delay=args.delay,
                deepen=not args.no_deepen,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[discover] FAIL {parent_code}: {exc}", flush=True)
            summary.append(
                {
                    "parent": parent_code,
                    "parent_name": parent.name,
                    "count": 0,
                    "with_appointment": 0,
                    "with_leader": 0,
                    "error": str(exc),
                }
            )
            continue
        entries = candidates_to_entries(candidates)
        all_entries.extend(entries)
        row = {
            "parent": parent_code,
            "parent_name": parent.name,
            "count": len(entries),
            "with_appointment": sum(1 for e in entries if e.get("appointment_list_url")),
            "with_leader": sum(1 for e in entries if e.get("leader_intro_url")),
        }
        summary.append(row)
        print(f"[discover] done {parent_code}: {row}", flush=True)

    payload = {"summary": summary, "count": len(all_entries), "entries": all_entries}
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    # Drop previous entries for the parents we just rescanned, then merge.
    kept = [e for e in load_city_registry(args.output) if e.get("parent_code") not in set(parents)]
    save_city_registry(kept + all_entries, path=args.output)
    reload_sites()
    print(
        json.dumps(
            {
                "wrote": str(args.output.resolve()),
                "summary": summary,
                "total": len(load_city_registry(args.output)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
