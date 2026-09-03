"""Classify list-head scan failures; defer buckets that need separate fixes.

See docs/crawl-pitfalls-2026-09-01.md before changing skip rules.
"""

from __future__ import annotations

# Sites confirmed to have no 人事任免 column (manual ingest / empty column audit).
# Do not retry list fetch; scan_bureau_list_heads returns skipped_no_list.
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
        # Jiangxi — ingest_jiangxi_manual
        "jiangxi_col31073",
        "jiangxi_col31076",
        # Fujian
        "fujian_fj_pingtanswj",
        # Beijing
        "beijing_yanshan",
        "beijing_jingkai",
        # Guizhou
        "guizhou_path_guian",
        # Sichuan — ingest_sichuan_manual (leader-only cities)
        "sichuan_col1153",
        "sichuan_col1183",
        "sichuan_col1363",
        "sichuan_col1423",
        "sichuan_col1513",
        "sichuan_col1545",
        "sichuan_col1635",
        # No appointment notices in DB + no list URL — not a scan failure (user confirmed)
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
    }
)

# Skip in default automated full scan — revisit per bucket, not blind retry-all.
DEFERRED_PREFIXES: tuple[str, ...] = (
    # 2026-09-02：官网侧异常（412 + 浏览器/CDP 均空）；用户确认先停，勿硬刷
    "hubei_hbsw_",
    "hubei",
)

DEFERRED_EXACT: frozenset[str] = frozenset()


def is_no_appt_site(code: str) -> bool:
    return code in KNOWN_NO_APPT


def is_deferred_list_head(code: str) -> bool:
    if code in DEFERRED_EXACT:
        return True
    if code in KNOWN_NO_APPT:
        return True
    return any(code.startswith(p) for p in DEFERRED_PREFIXES)


def filter_scan_codes(codes: list[str], *, include_deferred: bool = False) -> tuple[list[str], list[str]]:
    """Return (to_scan, skipped_deferred)."""
    if include_deferred:
        return list(codes), []
    kept: list[str] = []
    skipped: list[str] = []
    for code in codes:
        if is_deferred_list_head(code) or is_no_appt_site(code):
            skipped.append(code)
        else:
            kept.append(code)
    return kept, skipped
