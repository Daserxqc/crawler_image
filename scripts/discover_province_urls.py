from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.province_discovery import discover_all, discover_province
from tax_platform.config.province_urls_io import (
    DEFAULT_REGISTRY,
    entry_to_override,
    load_discovery_registry,
    merge_discovery_entry,
    merge_override,
    write_overrides,
)
from tax_platform.config.sites_provinces import PROVINCE_SUBDOMAINS

PROVINCE_CODES = [code for _sub, _region, code in PROVINCE_SUBDOMAINS if code != "shanghai"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe leader / appointment URLs (one province at a time).")
    parser.add_argument("--code", help="Single province code (recommended)")
    parser.add_argument("--all", action="store_true", help="Run all provinces sequentially (slow)")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--output", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--apply", action="store_true", help="Merge result into sites_provinces.py")
    parser.add_argument("--list", action="store_true", help="List province codes")
    return parser.parse_args()


def _subdomain_for(code: str) -> str:
    for subdomain, _region, item_code in PROVINCE_SUBDOMAINS:
        if item_code == code:
            return subdomain
    raise KeyError(f"Unknown province code: {code}")


def _apply_entry(entry: dict) -> None:
    override = entry_to_override(entry)
    if not override:
        return
    write_overrides(merge_override(entry["code"], override))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    out = Path(args.output)

    if args.list:
        print("\n".join(PROVINCE_CODES))
        return

    if args.all:
        results = discover_all(delay=args.delay, output=out)
        payload = [asdict(item) for item in results]
    elif args.code:
        code = args.code.strip().lower()
        result = discover_province(code, _subdomain_for(code), delay=args.delay)
        entry = asdict(result)
        payload = merge_discovery_entry(entry, path=out)
        if args.apply:
            _apply_entry(entry)
    else:
        raise SystemExit(
            "Specify one province: --code guangdong\n"
            "Or use scripts/crawl_province.py guangdong for discover+crawl+ingest."
        )

    if args.all:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.apply:
            for entry in payload:
                _apply_entry(entry)

    registry = load_discovery_registry(out)
    ok_leader = sum(1 for item in registry if item.get("leader_intro_url"))
    ok_appt = sum(1 for item in registry if item.get("appointment_list_url"))
    logging.info("Registry: %s provinces, leaders %s, appointments %s -> %s", len(registry), ok_leader, ok_appt, out)

    latest = payload[-1] if payload else None
    if latest:
        logging.info(
            "%s leader=%s(%s) appt=%s(%s)",
            latest["code"],
            latest.get("leader_intro_url"),
            latest.get("leader_count"),
            latest.get("appointment_list_url"),
            latest.get("appointment_count"),
        )


if __name__ == "__main__":
    main()
