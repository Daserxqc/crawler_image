"""Probe previously soft-failed sites; keep those that look like Beijing-style 412/WAF."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html, looks_like_waf_challenge
from tax_platform.crawler.list_heads_job import scan_bureau_list_heads
from tax_platform.store.list_heads import (
    DEFAULT_LIST_HEADS_DB_PATH,
    clear_list_scan_failure,
    connect_list_heads,
)
from tax_platform.store.schema import DEFAULT_DB_PATH, connect

# From the original soft-fail audit (not Sichuan/Jilin SSL hard fails).
CANDIDATES = [
    # Hebei cities
    "hebei_bdsw",
    "hebei_cdsw",
    "hebei_czsw",
    "hebei_dzsw",
    "hebei_hdsw",
    "hebei_hssw",
    "hebei_lfsw",
    "hebei_qhdsw",
    "hebei_sjzsw",
    "hebei_tssw",
    "hebei_xaxq",
    "hebei_xjsw",
    "hebei_xtsw",
    "hebei_zjksw",
    # Hubei cities
    "hubei_hbsw_huanggang",
    "hubei_hbsw_jingzhou",
    "hubei_hbsw_qianjiang",
    "hubei_hbsw_suizhou",
    "hubei_hbsw_wuhan",
    "hubei_hbsw_xiaogan",
    "hubei_hbsw_yichang",
    # Henan + Shenzhen (same soft-fail bucket as Beijing)
    "henan",
    "guangdong_shenzhen",
]


def probe_one(session, code: str) -> dict:
    site = get_site(code)
    url = (site.appointment_list_url or "").strip()
    out = {"bureau": code, "url": url, "ok": False, "items": 0, "error": None, "final": None}
    if not url:
        out["error"] = "no list url"
        return out
    try:
        final, html = fetch_html(session, url, follow_meta_refresh=False, retries=1)
        out["final"] = final
        if looks_like_waf_challenge(html):
            out["error"] = "still waf"
            return out
        items = parse_appointment_list(html, final if str(final).startswith("http") else url)
        out["items"] = len(items)
        out["ok"] = len(items) > 0 or len(html) > 5000
        if not out["ok"]:
            out["error"] = f"parsed 0 items (html={len(html)})"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:240]
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Smoke one Hebei first
    session = create_session()
    first = "hebei_sjzsw"
    logging.info("smoke probe %s", first)
    smoke = probe_one(session, first)
    print(json.dumps(smoke, ensure_ascii=False, indent=2))
    if not smoke.get("ok"):
        # still try CDP path is inside fetch_html; if smoke fails, stop batch
        print("SMOKE_FAILED")
        Path("output/waf412_retry_result.json").write_text(
            json.dumps({"smoke": smoke, "results": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return

    results = [smoke]
    for code in CANDIDATES:
        if code == first:
            continue
        logging.info("probe %s", code)
        results.append(probe_one(session, code))

    ok_codes = [r["bureau"] for r in results if r.get("ok")]
    main_conn = connect(DEFAULT_DB_PATH, light=True)
    heads_conn = connect_list_heads(DEFAULT_LIST_HEADS_DB_PATH, main_db_path=DEFAULT_DB_PATH)
    scan_rows = []
    for code in ok_codes:
        result = scan_bureau_list_heads(
            heads_conn, code, main_conn=main_conn, session=session, delay=0.2
        )
        scan_rows.append(
            {
                "bureau": code,
                "error": result.error,
                "list_count": result.list_count,
                "updated": result.updated,
                "seeded": result.seeded,
                "newest": result.crawled_newest,
            }
        )
        if not result.error:
            clear_list_scan_failure(heads_conn, code)
        heads_conn.commit()

    payload = {
        "probed": len(results),
        "fetch_ok": len(ok_codes),
        "fetch_fail": [r for r in results if not r.get("ok")],
        "fetch_ok_codes": ok_codes,
        "scans": scan_rows,
        "scan_recovered": sum(1 for r in scan_rows if not r.get("error")),
    }
    out = Path("output/waf412_retry_result.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("probed", "fetch_ok", "scan_recovered", "fetch_ok_codes")}, ensure_ascii=False, indent=2))
    heads_conn.close()
    main_conn.close()


if __name__ == "__main__":
    main()
