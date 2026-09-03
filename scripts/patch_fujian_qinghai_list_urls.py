# -*- coding: utf-8 -*-
"""Fix Fujian/Qinghai appointment_list_url to real list pages (user-confirmed)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites

# Fuzhou: jgsz/ is the 主动公开目录下人事任免 pane (user screenshot).
# Other cities: keep known working paths; try jgsz where currently rsxx_1009.
FUJIAN: dict[str, str] = {
    "fujian_fj_fzsswj": "https://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/jgsz/",
    # 莆田已是 rsrm/；其余先统一试 jgsz/（与福州同目录结构）
    "fujian_fj_qzsswj": "https://fujian.chinatax.gov.cn/qzsswj/zfxxgkzl/zfxxgkml/jgsz/",
    "fujian_fj_zzsswj": "https://fujian.chinatax.gov.cn/zzsswj/zfxxgkzl/zfxxgkml/jgsz/",
    "fujian_fj_npsswj": "https://fujian.chinatax.gov.cn/npsswj/zfxxgkzl/zfxxgkml/jgsz/",
    "fujian_fj_ndsswj": "https://fujian.chinatax.gov.cn/ndsswj/zfxxgkzl/zfxxgkml/jgsz/",
    "fujian_fj_lysswj": "https://fujian.chinatax.gov.cn/lysswj/zfxxgkzl/zfxxgkml/jgsz/",
    "fujian_fj_smsswj": "https://fujian.chinatax.gov.cn/smsswj/zfxxgkzl/zfxxgkml/jgsz/",
    # 莆田保持 rsrm（ingest 已验证）
    "fujian_fj_ptsswj": "https://fujian.chinatax.gov.cn/ptsswj/zfxxgkzl/zfxxgkml/rsrm/",
}

# Qinghai: hbz already uses /rsrm/xxgk_fdzd_list.shtml; notices live under /{slug}/rsrm/.
# commonlistmore is a JS shell — wrong for list-head monitoring.
QINGHAI_SLUGS = ("xns", "glz", "hds", "hnz", "hxz", "ysz", "kfq", "hbzz", "hbz")


def main() -> None:
    rows = load_city_registry()
    n = 0
    for row in rows:
        code = row.get("code") or ""
        want = None
        if code in FUJIAN:
            want = FUJIAN[code]
        elif code.startswith("qinghai_abbr_"):
            slug = code.removeprefix("qinghai_abbr_")
            if slug in QINGHAI_SLUGS:
                want = f"http://qinghai.chinatax.gov.cn/{slug}/rsrm/xxgk_fdzd_list.shtml"
        if not want:
            continue
        if row.get("appointment_list_url") != want:
            row["appointment_list_url"] = want
            notes = list(row.get("notes") or [])
            notes.append("list_url_corrected_user")
            row["notes"] = notes
            n += 1
            print(code, "->", want)
    if n:
        save_city_registry(rows)
        reload_sites()
    print(f"patched {n}")


if __name__ == "__main__":
    main()
