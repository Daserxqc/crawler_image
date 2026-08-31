# -*- coding: utf-8 -*-
"""Prune discovery-noise city sites from registry + DB; normalize NO_APPT notes."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import DEFAULT_CITY_REGISTRY, load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

# Link labels / fabricated bureau names that are not real city hubs.
JUNK_NAME_RE = re.compile(
    r"(通知公告|纳税咨询|办税指南|办税日历|优化营商|时政要闻|银税互动|发票举报|"
    r"互动交流|意见征集|新闻动态|热点问答|纳税人学堂|基层动态|我要查询|隐私声明|"
    r"媒体视点|专题专栏|下载中心|长者专区|三分局市税务局|"
    r"下载中心|我要查询|基层动态|纳税人学堂)"
)

# Explicit codes always treated as noise (even if name later changes).
JUNK_CODES = frozenset(
    {
        "chongqing_qxtax_dsfj",
        "guangxi_path_zzzq",
    }
)

# Real cities/districts whose appointment column is confirmed empty / missing.
NO_APPT_CODES: dict[str, str] = {
    # Anhui
    "anhui_col9477": "站点无人事任免栏目",
    "anhui_col9478": "站点无人事任免栏目",
    "anhui_col9480": "站点无人事任免栏目",
    # Jiangxi
    "jiangxi_col31073": "站点无人事任免栏目",
    "jiangxi_col31076": "站点无人事任免栏目",
    # Henan
    "henan_anyang": "站点无人事任免栏目",
    "henan_path_anyang": "站点无人事任免栏目",
    # Jilin / Neimenggu
    "jilin_col824": "站点无人事任免栏目",
    "jilin_col841": "站点无人事任免栏目",
    "neimenggu_xamswj": "站点无人事任免栏目",
    # Fujian / Guizhou / Hunan / Ningxia / Xizang / Beijing
    "fujian_fj_pingtanswj": "站点无人事任免栏目",
    "guizhou_sjpd_gaxqgwh": "站点无人事任免栏目",
    "hunan_path_xjxq": "站点无人事任免栏目",
    "ningxia_col12555": "站点无人事任免栏目",
    "ningxia_col12609": "站点无人事任免栏目",
    "xizang_abbr_lskf": "站点无人事任免栏目",
    "beijing_jingkai": "站点无人事任免栏目",
    "beijing_yanshan": "站点无人事任免栏目",
    # Sichuan (manual NO_APPT)
    "sichuan_col1153": "站点无人事任免栏目",
    "sichuan_col1183": "站点无人事任免栏目",
    "sichuan_col1363": "站点无人事任免栏目",
    "sichuan_col1423": "站点无人事任免栏目",
    "sichuan_col1513": "站点无人事任免栏目",
    "sichuan_col1545": "站点无人事任免栏目",
    "sichuan_col1635": "站点无人事任免栏目",
    # Hubei empty lists
    "hubei_hbsw_enshi": "栏目无发文",
    "hubei_hbsw_ezhou": "栏目无发文",
    "hubei_hbsw_jingmen": "栏目无发文",
    "hubei_hbsw_shennongjia": "栏目无发文",
    "hubei_hbsw_shiyan": "栏目无发文",
    "hubei_hbsw_tianmen": "栏目无发文",
    "hubei_hbsw_xiangyang": "栏目无发文",
    "hubei_hbsw_xianning": "栏目无发文",
    "hubei_hbsw_xiantao": "栏目无发文",
    # Tianjin empty lists
    "tianjin_fjdm_11242000000": "栏目无发文",
    "tianjin_fjdm_11243000000": "栏目无发文",
    "tianjin_fjdm_11244000000": "栏目无发文",
    "tianjin_fjdm_11248000000": "栏目无发文",
    "tianjin_fjdm_11256000000": "栏目无发文",
    "tianjin_fjdm_11294000000": "栏目无发文",
    "tianjin_fjdm_11297000000": "栏目无发文",
    "tianjin_fjdm_11298000000": "栏目无发文",
    "tianjin_fjdm_11299000000": "栏目无发文",
}


def _is_junk(entry: dict) -> bool:
    code = entry.get("code") or ""
    name = entry.get("name") or ""
    if code in JUNK_CODES:
        return True
    if JUNK_NAME_RE.search(name):
        return True
    return False


def _ensure_note(notes, token: str) -> list:
    if isinstance(notes, str):
        notes = [notes] if notes else []
    elif not isinstance(notes, list):
        notes = []
    out = list(notes)
    if token not in out:
        out.append(token)
    return out


def _purge_bureau(conn, code: str) -> dict[str, int]:
    stats = {}
    for table, col in (
        ("appointment_events", "bureau_code"),
        ("notices", "bureau_code"),
        ("leader_duties", "bureau_code"),
        ("persons", "bureau_code"),
        ("data_anomalies", "bureau_code"),
    ):
        try:
            stats[table] = conn.execute(f"DELETE FROM {table} WHERE {col}=?", (code,)).rowcount
        except Exception:
            stats[table] = 0
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--registry", type=Path, default=DEFAULT_CITY_REGISTRY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    entries = load_city_registry(args.registry)
    junk = [e for e in entries if _is_junk(e)]
    keep = [e for e in entries if not _is_junk(e)]

    # Resolve henan anyang by name if code differs
    for e in keep:
        name = e.get("name") or ""
        code = e.get("code") or ""
        if "安阳" in name and code not in NO_APPT_CODES:
            NO_APPT_CODES[code] = "站点无人事任免栏目"

    report = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "before": len(entries),
        "junk_removed": [
            {"code": e.get("code"), "name": e.get("name"), "parent": e.get("parent_code")}
            for e in junk
        ],
        "db_purged": {},
        "no_appt_marked": [],
        "after": len(keep),
    }

    logging.info("registry %s -> remove %s junk -> %s", len(entries), len(junk), len(keep))
    for e in junk:
        logging.info("JUNK %s %s", e.get("code"), e.get("name"))

    # Mark NO_APPT on keepers
    for e in keep:
        code = e.get("code") or ""
        if code not in NO_APPT_CODES:
            continue
        reason = NO_APPT_CODES[code]
        # Only clear URL when the column itself is missing; keep empty-list URLs for recheck.
        if reason.startswith("站点无"):
            e["appointment_list_url"] = None
        e["notes"] = _ensure_note(e.get("notes"), "NO_APPT")
        e["notes"] = _ensure_note(e["notes"], reason)
        report["no_appt_marked"].append({"code": code, "name": e.get("name"), "reason": reason})

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # Backup registry
    backup = args.registry.with_suffix(
        f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    shutil.copy2(args.registry, backup)
    logging.info("backup %s", backup)

    save_city_registry(keep, args.registry)
    reload_sites()

    conn = connect(args.db)
    try:
        for e in junk:
            code = e.get("code") or ""
            if not code:
                continue
            stats = _purge_bureau(conn, code)
            report["db_purged"][code] = stats
            logging.info("purged %s %s", code, stats)
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
    finally:
        conn.close()

    out = Path("output/prune_noise_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("wrote %s", out)
    print(json.dumps({"removed": len(junk), "registry": len(keep), "no_appt": len(report["no_appt_marked"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
