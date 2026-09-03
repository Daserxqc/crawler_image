# -*- coding: utf-8 -*-
"""Find bureaus that should skip list-head scan (no appointment column / never had data)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.list_heads_buckets import KNOWN_NO_APPT, classify_no_appt_candidate
from tax_platform.config.sites import list_sites, reload_sites
from tax_platform.store.schema import connect, DEFAULT_DB_PATH


def main() -> None:
    reload_sites()
    conn = connect(DEFAULT_DB_PATH, light=True)
    skip_no_url: list[dict] = []
    skip_known: list[dict] = []
    skip_zero_data: list[dict] = []
    monitor: list[dict] = []

    for site in list_sites():
        code = site.code
        appt = (site.appointment_list_url or "").strip()
        events = conn.execute(
            "SELECT COUNT(*) FROM appointment_events WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        notices = conn.execute(
            "SELECT COUNT(*) FROM notices WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        row = {
            "code": code,
            "name": site.name,
            "events": events,
            "notices": notices,
            "appt_url": appt[:80] if appt else "",
        }
        if code in KNOWN_NO_APPT:
            skip_known.append(row)
        elif not appt:
            skip_no_url.append(row)
        elif events == 0 and notices == 0:
            skip_zero_data.append(row)
        else:
            monitor.append(row)

    print(f"KNOWN_NO_APPT: {len(skip_known)}")
    for r in skip_known:
        print(f"  {r['code']:32} ev={r['events']} url={r['appt_url'][:50]}")
    print(f"\nNO_URL (skip scan): {len(skip_no_url)}")
    for r in skip_no_url[:15]:
        print(f"  {r['code']:32} ev={r['events']}")
    if len(skip_no_url) > 15:
        print(f"  ... +{len(skip_no_url) - 15} more")
    print(f"\nHAS_URL but ZERO appt data (candidates for NO_APPT): {len(skip_zero_data)}")
    for r in skip_zero_data:
        print(f"  {r['code']:32} ev={r['events']} nt={r['notices']} {r['appt_url'][:60]}")
    print(f"\nShould monitor: {len(monitor)}")

    out = {
        "known_no_appt": skip_known,
        "no_url": skip_no_url,
        "zero_data_with_url": skip_zero_data,
    }
    path = ROOT / "output" / "no_appt_audit.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {path}")
    conn.close()


if __name__ == "__main__":
    main()
