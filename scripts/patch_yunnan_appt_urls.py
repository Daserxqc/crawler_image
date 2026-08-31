# -*- coding: utf-8 -*-
"""Patch Yunnan city appointment_list_url from BFS probe results."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites

BFS = ROOT / "output" / "yunnan_appt_cols.json"
PROVINCIAL = "https://yunnan.chinatax.gov.cn/col/col8641/index.html"


def main() -> None:
    if not BFS.exists():
        raise SystemExit(f"missing {BFS}")

    rows = json.loads(BFS.read_text(encoding="utf-8"))
    by_name = {
        row["city"]: row["best"]["url"]
        for row in rows
        if row.get("best") and int(row["best"].get("match") or 0) > 0
    }

    registry = load_city_registry()
    updated = 0
    for entry in registry:
        if not str(entry.get("code", "")).startswith("yunnan_col"):
            continue
        url = by_name.get(entry.get("name", ""))
        if not url:
            continue
        notes = list(entry.get("notes") or [])
        if "yunnan_manual_appt" in notes:
            continue
        if entry.get("appointment_list_url") != url:
            entry["appointment_list_url"] = url
            notes = list(entry.get("notes") or [])
            if "yunnan_appt_bfs" not in notes:
                notes.append("yunnan_appt_bfs")
            entry["notes"] = notes
            updated += 1

    # Provincial yunnan entry if present
    for entry in registry:
        if entry.get("code") == "yunnan":
            entry["appointment_list_url"] = PROVINCIAL
            updated += 1
            break

    save_city_registry(registry)
    reload_sites()
    print(f"patched {updated} yunnan appointment_list_url entries")


if __name__ == "__main__":
    main()
