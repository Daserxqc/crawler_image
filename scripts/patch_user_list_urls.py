# -*- coding: utf-8 -*-
"""Patch the 7 user-provided appointment list URLs; mark empty-no-url as known skip."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites

URLS: dict[str, str] = {
    "ningxia_col11032": "http://ningxia.chinatax.gov.cn/col/col14547/index.html",  # 银川
    "ningxia_col11423": "http://ningxia.chinatax.gov.cn/col/col14559/index.html",  # 吴忠
    "ningxia_col11766": "http://ningxia.chinatax.gov.cn/col/col14573/index.html",  # 中卫
    "ningxia_col12013": "http://ningxia.chinatax.gov.cn/col/col14566/index.html",  # 固原
    "ningxia_col12356": "http://ningxia.chinatax.gov.cn/col/col14555/index.html",  # 石嘴山
    "jiangxi_col31072": "https://jiangxi.chinatax.gov.cn/col/col39038/index.html",  # 宜春 (#div 忽略)
    "sichuan_col1393": "https://sichuan.chinatax.gov.cn/col/col16196/index.html?number=A0020",  # 广安
}

# No notices + no list URL: not a failure; skip list-head monitoring.
KNOWN_EMPTY_NO_URL: tuple[str, ...] = (
    "anhui_col9477",
    "anhui_col9478",
    "anhui_col9480",
    "guizhou_sjpd_gaxqgwh",
    "henan_path_anyang",
    "neimenggu_xamswj",
    "ningxia_col12555",
    "ningxia_col12609",
    "tianjin_fjdm_11248000000",
    "xizang_abbr_lskf",
)


def main() -> None:
    rows = load_city_registry()
    patched = 0
    for row in rows:
        code = row.get("code") or ""
        if code in URLS:
            want = URLS[code]
            if row.get("appointment_list_url") != want:
                row["appointment_list_url"] = want
                notes = list(row.get("notes") or [])
                notes.append("appt_url_user_provided")
                row["notes"] = notes
                patched += 1
    save_city_registry(rows)
    reload_sites()
    print(f"patched {patched} appointment_list_url")


if __name__ == "__main__":
    main()
