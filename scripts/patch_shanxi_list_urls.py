# -*- coding: utf-8 -*-
"""Normalize Shanxi city appointment_list_url to son/list/{abbr}-{orgid}-4187."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites

# code suffix → (abbr, orgid) from ingest_shanxi_manual CITY_TO_CODE
SHANXI_SLUGS: dict[str, tuple[str, str]] = {
    "shanxi_son_ty_11401": ("ty", "11401"),
    "shanxi_son_dt_11402": ("dt", "11402"),
    "shanxi_son_yq_11403": ("yq", "11403"),
    "shanxi_son_cz_11404": ("cz", "11404"),
    "shanxi_son_jc_11405": ("jc", "11405"),
    "shanxi_son_sz_11406": ("sz", "11406"),
    "shanxi_son_sf_11407": ("sf", "11407"),
    "shanxi_son_xz_11422": ("xz", "11422"),
    "shanxi_son_ll_11423": ("ll", "11423"),
    "shanxi_son_jz_11424": ("jz", "11424"),
    "shanxi_son_lf_11426": ("lf", "11426"),
    "shanxi_son_yc_11427": ("yc", "11427"),
}


def main() -> None:
    rows = load_city_registry()
    n = 0
    for row in rows:
        code = row.get("code") or ""
        slug = SHANXI_SLUGS.get(code)
        if not slug:
            continue
        abbr, orgid = slug
        want = f"http://shanxi.chinatax.gov.cn/son/list/{abbr}-{orgid}-4187"
        if row.get("appointment_list_url") != want:
            row["appointment_list_url"] = want
            notes = row.get("notes")
            if isinstance(notes, list):
                notes.append("son_list_url_normalized")
            n += 1
    if n:
        save_city_registry(rows)
        reload_sites()
    print(f"patched {n} shanxi city URLs")


if __name__ == "__main__":
    main()
