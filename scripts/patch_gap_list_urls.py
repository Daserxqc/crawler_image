# -*- coding: utf-8 -*-
"""Patch list URLs for gap sites (beijing miyun/yanqing, shanxi/heilongjiang province)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites

MANUAL = ROOT / "output" / "manual" / "beijing"

PATCHES: dict[str, str] = {
    "beijing_miyun": "http://beijing.chinatax.gov.cn/bjswj/c106486/xxgk_nr_fjmy.shtml",
    "beijing_yanqing": "http://beijing.chinatax.gov.cn/bjswj/c106488/xxgk_nr_fjyq.shtml",
    "shanxi": "http://shanxi.chinatax.gov.cn/son/list/sx-11400-4187",
    "heilongjiang": "http://heilongjiang.chinatax.gov.cn/col/col17418/index.html",
}


def _saved_appt_url(code: str) -> str | None:
    appt = MANUAL / code / "appt_list.html"
    if not appt.is_file():
        return None
    text = appt.read_text(encoding="utf-8", errors="ignore")[:800]
    m = re.search(r"saved from url=\([^)]+\)(https?://[^\s>]+)", text, re.I)
    return m.group(1).strip() if m else None


def main() -> None:
    rows = load_city_registry()
    n = 0
    for row in rows:
        code = row.get("code") or ""
        if code in PATCHES:
            want = PATCHES[code]
            if row.get("appointment_list_url") != want:
                row["appointment_list_url"] = want
                notes = row.get("notes")
                if isinstance(notes, list):
                    notes.append("gap_list_url_patch")
                n += 1
            continue
        if code.startswith("beijing_") and code not in {"beijing_yanshan", "beijing_jingkai"}:
            live = _saved_appt_url(code)
            cur = (row.get("appointment_list_url") or "").strip()
            if live and cur.startswith("file:"):
                row["appointment_list_url"] = live
                notes = row.get("notes")
                if isinstance(notes, list):
                    notes.append("beijing_live_list_url")
                n += 1

    # province rows live in sites_provinces.py — patch registry if present
    for code, want in PATCHES.items():
        if code.startswith("beijing"):
            continue
        for row in rows:
            if row.get("code") == code and row.get("appointment_list_url") != want:
                row["appointment_list_url"] = want
                n += 1

    if n:
        save_city_registry(rows)
        reload_sites()
    print(f"patched {n} list URLs")


if __name__ == "__main__":
    main()
