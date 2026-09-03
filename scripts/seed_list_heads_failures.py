"""Seed open scan failures from the prior all-sites run, then exit."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tax_platform.config.sites import ALL_SITES
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    connect_list_heads,
    list_open_scan_failures,
    record_list_scan_failure,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"


def main() -> None:
    summary = json.loads((OUT / "list_heads_all.json").read_text(encoding="utf-8"))
    hard = {e["bureau"]: e["error"] for e in summary["scan"]["errors"]}

    raw = (OUT / "list_heads_all.log").read_bytes()
    text = (
        raw.decode("utf-16")
        if (len(raw) > 1 and raw[1] == 0) or raw[:2] == b"\xff\xfe"
        else raw.decode("utf-8", errors="replace")
    )

    url_to_codes: dict[str, set[str]] = {}
    for s in ALL_SITES:
        u = (s.appointment_list_url or "").strip()
        if not u:
            continue
        for cand in {
            u,
            u.rstrip("/"),
            u + "/",
            u.replace("http://", "https://"),
            u.replace("https://", "http://"),
        }:
            url_to_codes.setdefault(cand, set()).add(s.code)

    soft: dict[str, str] = {}
    for ln in text.splitlines():
        if "list fetch failed" not in ln:
            continue
        m = re.search(r"list fetch failed (\S+?): (.*)$", ln)
        if not m:
            continue
        url, msg = m.group(1), m.group(2)
        codes = url_to_codes.get(url) or url_to_codes.get(url.rstrip("/")) or set()
        for code in codes:
            soft[code] = msg

    # Prefer hard error text when both present
    all_fails = dict(soft)
    all_fails.update(hard)

    conn = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=OUT / "tax_hr.db")
    for code, err in sorted(all_fails.items()):
        try:
            site = next(s for s in ALL_SITES if s.code == code)
            list_url = (site.appointment_list_url or "").strip()
        except StopIteration:
            list_url = ""
        record_list_scan_failure(conn, code, list_url=list_url, error=err[:500])
    conn.commit()
    open_rows = list_open_scan_failures(conn)
    print(json.dumps({"seeded": len(all_fails), "open_failures": len(open_rows)}, ensure_ascii=False))
    print("\n".join(sorted(all_fails)))
    conn.close()


if __name__ == "__main__":
    import json

    main()
