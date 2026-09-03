# -*- coding: utf-8 -*-
"""Compare open list-head failures against DB — find false failures (no appt column)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import get_site, reload_sites
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    connect_list_heads,
    list_open_scan_failures,
)
from tax_platform.store.schema import DEFAULT_DB_PATH, connect

# Curated from manual ingest scripts (站点无人事任免 / list=0 empty column).
KNOWN_NO_APPT: frozenset[str] = frozenset(
    {
        # Hubei — ingest_hubei_manual.LIKELY_EMPTY_APPT_CODES
        "hubei_hbsw_enshi",
        "hubei_hbsw_ezhou",
        "hubei_hbsw_jingmen",
        "hubei_hbsw_shennongjia",
        "hubei_hbsw_shiyan",
        "hubei_hbsw_tianmen",
        "hubei_hbsw_xiangyang",
        "hubei_hbsw_xianning",
        "hubei_hbsw_xiantao",
        # Jilin — prune_registry_noise
        "jilin_col824",
        "jilin_col841",
        # Jiangxi
        "jiangxi_col31073",
        "jiangxi_col31076",
        # Fujian
        "fujian_fj_pingtanswj",
        # Beijing
        "beijing_yanshan",
        "beijing_jingkai",
        # Guizhou
        "guizhou_path_guian",
    }
)


def classify(*, appt_url: bool, events: int, notices: int, leaders: int, heads: int, code: str) -> str:
    if code in KNOWN_NO_APPT:
        return "known_no_appt"
    if not appt_url and events == 0 and notices == 0:
        return "no_url_no_data"
    if events == 0 and notices == 0 and heads == 0 and not appt_url:
        return "no_url_no_data"
    if events == 0 and notices == 0 and heads == 0:
        return "zero_appt_data"
    return "real_failure"


def main() -> None:
    reload_sites()
    main_conn = connect(DEFAULT_DB_PATH, light=True)
    heads_conn = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=DEFAULT_DB_PATH)
    fails = list_open_scan_failures(heads_conn)
    rows = []
    for f in fails:
        code = f["bureau_code"]
        try:
            site = get_site(code)
            name = site.name
            appt = (site.appointment_list_url or "").strip()
        except KeyError:
            name = "?"
            appt = ""
        events = main_conn.execute(
            "SELECT COUNT(*) FROM appointment_events WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        notices = main_conn.execute(
            "SELECT COUNT(*) FROM notices WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        leaders = main_conn.execute(
            "SELECT COUNT(*) FROM leader_duties WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        heads = heads_conn.execute(
            "SELECT COUNT(*) FROM appointment_list_heads WHERE bureau_code = ?", (code,)
        ).fetchone()[0]
        bucket = classify(
            appt_url=bool(appt),
            events=events,
            notices=notices,
            leaders=leaders,
            heads=heads,
            code=code,
        )
        rows.append(
            {
                "code": code,
                "name": name,
                "bucket": bucket,
                "appt_url": bool(appt),
                "events": events,
                "notices": notices,
                "leaders": leaders,
                "heads": heads,
            }
        )

    from collections import Counter

    counts = Counter(r["bucket"] for r in rows)
    print(f"OPEN FAILURES: {len(rows)}")
    for k, v in counts.most_common():
        print(f"  {k}: {v}")
    print()
    for bucket in ("known_no_appt", "no_url_no_data", "zero_appt_data", "real_failure"):
        grp = [r for r in rows if r["bucket"] == bucket]
        if not grp:
            continue
        print(f"=== {bucket} ({len(grp)}) ===")
        for r in grp:
            print(
                f"  {r['code']:32} url={str(r['appt_url']):5} "
                f"ev={r['events']:4} nt={r['notices']:4} ld={r['leaders']:3} heads={r['heads']}"
            )

    out = ROOT / "output" / "list_failures_audit.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    heads_conn.close()
    main_conn.close()


if __name__ == "__main__":
    main()
