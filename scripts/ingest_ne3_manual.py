"""Ingest Hebei / Jilin / Heilongjiang from user-saved HTML; headless-only notice fetch."""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.crawler.leader_intro import parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.store import ingest_appointment_results, ingest_leader_results, list_persons

OUT = ROOT / "output"
APPT_LIMIT = 15

PROVINCES: dict[str, dict[str, object]] = {
    "hebei": {
        "region": "河北",
        "leader_needles": ("河北", "领导简介"),
        "leader_fallback": "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html",
        "appt_fallback": "http://hebei.chinatax.gov.cn/hbswxxgk/gkml/1166/1247/1757/index.html",
        "appt_from_files": True,
    },
    "jilin": {
        "region": "吉林",
        "leader_needles": ("吉林", "领导"),
        "leader_fallback": "https://jilin.chinatax.gov.cn/col/col24472/index.html",
        "appt_needles": ("吉林", "人事"),
        "appt_fallback": "https://jilin.chinatax.gov.cn/col/col8211/index.html",
        "appt_from_files": False,
    },
    "heilongjiang": {
        "region": "黑龙江",
        "leader_needles": ("黑龙江", "领导简介"),
        "leader_fallback": "http://heilongjiang.chinatax.gov.cn/col/col11194/index.html",
        "appt_needles": ("黑龙江", "人事"),
        "appt_fallback": "http://heilongjiang.chinatax.gov.cn/col/col11190/index.html",
        "appt_from_files": False,
    },
}


def _newest_html(*needles: str) -> Path:
    cands = [p for p in OUT.glob("*.html") if all(n in p.name for n in needles)]
    if not cands:
        raise FileNotFoundError(f"No HTML in {OUT} matching {needles}")
    return max(cands, key=lambda p: p.stat().st_mtime)


def _hebei_appt_list_html() -> Path:
    for files_dir in sorted(OUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not files_dir.is_dir() or "河北" not in files_dir.name:
            continue
        candidate = files_dir / "1167.html"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Hebei appointment list HTML (1167.html) not found under output/*河北*_files")


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _saved_url(html: str, fallback: str) -> str:
    match = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html)
    return match.group(1) if match else fallback


def ingest_leaders(code: str) -> dict:
    cfg = PROVINCES[code]
    path = _newest_html(*cfg["leader_needles"])  # type: ignore[arg-type]
    html = _read_html(path)
    url = _saved_url(html, str(cfg["leader_fallback"]))
    # Jilin detail page embeds full sidebar list; prefer hub URL when present in links.
    if code == "jilin" and "col24472" not in url:
        url = str(cfg["leader_fallback"])
    leaders = parse_leader_intro(html, url, code)
    result = LeaderCrawlResult(bureau=code, hub_url=url, page_count=1, leaders=leaders)
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / f"{code}_leaders.json", payload)
    ingested = ingest_leader_results(payload)
    return {
        "source": path.name,
        "url": url,
        "leaders": len(leaders),
        "ingested": ingested,
        "names": [d.person_name for d in leaders],
    }


def ingest_appointments(code: str, *, try_live: bool = True) -> dict:
    cfg = PROVINCES[code]
    if cfg.get("appt_from_files"):
        list_html_path = _hebei_appt_list_html()
    else:
        list_html_path = _newest_html(*cfg["appt_needles"])  # type: ignore[arg-type]

    list_html = _read_html(list_html_path)
    list_url = _saved_url(list_html, str(cfg["appt_fallback"]))
    items = parse_appointment_list(list_html, list_url)[:APPT_LIMIT]

    notices = []
    events = []
    failed: list[dict[str, str]] = []
    session = create_session() if try_live else None

    for item in items:
        if not try_live or session is None:
            failed.append({"url": item.source_url, "error": "skipped live fetch (manual list only)"})
            continue
        try:
            time.sleep(0.2)
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
                allow_browser=True,
            )
            notice = parse_appointment_detail(detail_html, detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)[:500]})
            logging.warning("Notice fetch failed %s: %s", item.source_url, exc)

    result = AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / f"{code}_appointments.json", payload)
    new_events = ingest_appointment_results(payload)
    return {
        "source": list_html_path.name if list_html_path.parent == OUT else str(list_html_path.relative_to(OUT)),
        "url": list_url,
        "list_items": len(items),
        "notices": len(notices),
        "events": len(events),
        "failed": len(failed),
        "new_events_ingested": new_events,
    }


def _probe_live(code: str) -> bool:
    cfg = PROVINCES[code]
    if cfg.get("appt_from_files"):
        list_path = _hebei_appt_list_html()
    else:
        list_path = _newest_html(*cfg["appt_needles"])  # type: ignore[arg-type]
    list_html = _read_html(list_path)
    list_url = _saved_url(list_html, str(cfg["appt_fallback"]))
    items = parse_appointment_list(list_html, list_url)[:1]
    if not items:
        return False
    try:
        session = create_session()
        fetch_html(session, items[0].source_url, referer=list_url, allow_browser=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.warning("%s headless notice probe failed — list-only mode: %s", code, exc)
        return False


def run_province(code: str) -> dict:
    summary: dict = {"code": code, "mode": "manual+headless"}
    summary["leaders"] = ingest_leaders(code)
    try_live = _probe_live(code)
    summary["appointments"] = ingest_appointments(code, try_live=try_live)
    persons = list(list_persons(bureau_code=code))
    summary["persons_in_db"] = len(persons)
    summary["L"] = summary["leaders"]["leaders"]
    summary["E"] = summary["appointments"]["events"]
    summary["leader_file"] = summary["leaders"]["source"]
    summary["appt_file"] = summary["appointments"]["source"]
    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    codes = sys.argv[1:] or ["hebei", "jilin", "heilongjiang"]
    rows = []
    for code in codes:
        if code not in PROVINCES:
            raise SystemExit(f"Unknown province {code}; choose from {sorted(PROVINCES)}")
        logging.info("=== ingest %s ===", code)
        rows.append(run_province(code))
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
