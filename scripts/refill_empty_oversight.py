"""Refill empty-oversight province leaders (+ optional STA / Hainan) via curl.exe.

Local requests/playwright often hit SSL EOF or chrome-error; curl works for many
province hosts. Writes JSON under output/refill/ then ingests into SQLite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.crawler.jpage import materialize_list_html
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.models.entities import to_dict
from tax_platform.store import ingest_appointment_results, ingest_leader_results
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

EMPTY_OVERSIGHT = [
    "chongqing",
    "fujian",
    "gansu",
    "guangdong",
    "hainan",
    "hebei",
    "heilongjiang",
    "henan",
    "jiangxi",
    "jilin",
    "ningxia",
    "qinghai",
    "shaanxi",
    "xinjiang",
    "yunnan",
]

OUT = ROOT / "output" / "refill"


def curl_get(url: str, *, timeout: int = 40, referer: str | None = None) -> str:
    cmd = [
        "curl.exe",
        "-sL",
        "--max-time",
        str(timeout),
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--compressed",
    ]
    if referer:
        cmd.extend(["-e", referer])
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"curl exit {proc.returncode}: {proc.stderr[:200]!r}")
    # Prefer utf-8; fall back.
    raw = proc.stdout
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def crawl_leaders_curl(code: str, *, delay: float = 0.35) -> LeaderCrawlResult:
    from tax_platform.models.entities import LeaderDuty

    site = get_site(code)
    hub_url = site.leader_intro_url
    hub_html = curl_get(hub_url)
    pages = [hub_url]
    for target in leader_page_targets(hub_html, hub_url):
        if target not in pages:
            pages.append(target)

    by_name: dict[str, LeaderDuty] = {}
    failed: list[dict[str, str]] = []

    def remember(duty: LeaderDuty) -> None:
        prev = by_name.get(duty.person_name)
        if prev is None:
            by_name[duty.person_name] = duty
            return
        prev_score = (
            len(prev.departments_raw or []),
            len(prev.duty_summary or ""),
            len(prev.title_raw or ""),
        )
        new_score = (
            len(duty.departments_raw or []),
            len(duty.duty_summary or ""),
            len(duty.title_raw or ""),
        )
        if new_score > prev_score:
            by_name[duty.person_name] = duty

    for duty in parse_leader_intro(hub_html, hub_url, code):
        remember(duty)

    for i, page_url in enumerate(pages):
        if i == 0:
            continue
        if delay:
            time.sleep(delay)
        try:
            html = curl_get(page_url, referer=hub_url)
            for duty in parse_leader_intro(html, page_url, code):
                remember(duty)
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": page_url, "error": str(exc)})

    return LeaderCrawlResult(
        bureau=code,
        hub_url=hub_url,
        page_count=len(pages),
        leaders=list(by_name.values()),
        failed=failed,
    )


def crawl_appointments_curl(code: str, *, limit: int = 40, delay: float = 0.35) -> dict:
    site = get_site(code)
    list_url = site.appointment_list_url
    list_html = materialize_list_html(curl_get(list_url))
    items = parse_appointment_list(list_html, list_url)
    if limit > 0:
        items = items[:limit]
    notices = []
    events = []
    failed = []
    for i, item in enumerate(items):
        if delay and i:
            time.sleep(delay)
        try:
            html = curl_get(item.source_url, referer=list_url)
            notice = parse_appointment_detail(html, item.source_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)})
    return {
        "bureau": code,
        "list_url": list_url,
        "list_count": len(items),
        "notices": [to_dict(n) for n in notices],
        "events": [to_dict(e) for e in events],
        "failed": failed,
    }


def leaders_with_deps(result: LeaderCrawlResult) -> int:
    return sum(1 for d in result.leaders if d.departments_raw)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sites",
        default=",".join(EMPTY_OVERSIGHT),
        help="Comma-separated bureau codes for leader refill",
    )
    parser.add_argument("--include-sta", action="store_true", help="Also crawl sta leaders")
    parser.add_argument("--hainan-appointments", action="store_true", default=True)
    parser.add_argument("--no-hainan-appointments", action="store_true")
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    codes = [c.strip() for c in args.sites.split(",") if c.strip()]
    if args.include_sta and "sta" not in codes:
        codes.append("sta")

    summary: list[dict] = []
    leader_payloads: list[dict] = []

    for code in codes:
        print(f"=== leaders {code} ===", flush=True)
        try:
            result = crawl_leaders_curl(code, delay=args.delay)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL hub: {exc}", flush=True)
            summary.append({"code": code, "ok": False, "error": str(exc)})
            continue
        path = dump_json(OUT / f"{code}_leaders.json", serialize_crawl_result(result))
        n_deps = leaders_with_deps(result)
        print(
            f"  people={len(result.leaders)} with_deps={n_deps} "
            f"pages={result.page_count} failed={len(result.failed)} -> {path}",
            flush=True,
        )
        summary.append(
            {
                "code": code,
                "ok": True,
                "people": len(result.leaders),
                "with_deps": n_deps,
                "pages": result.page_count,
                "failed": len(result.failed),
            }
        )
        if result.leaders:
            leader_payloads.append(serialize_crawl_result(result))

    appt_payloads: list[dict] = []
    if not args.no_hainan_appointments:
        print("=== appointments hainan ===", flush=True)
        try:
            appt = crawl_appointments_curl("hainan", delay=args.delay)
            path = dump_json(OUT / "hainan_appointments.json", appt)
            print(
                f"  notices={len(appt['notices'])} events={len(appt['events'])} "
                f"failed={len(appt['failed'])} -> {path}",
                flush=True,
            )
            summary.append(
                {
                    "code": "hainan_appointments",
                    "ok": True,
                    "notices": len(appt["notices"]),
                    "events": len(appt["events"]),
                }
            )
            if appt["notices"] or appt["events"]:
                appt_payloads.append(appt)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL: {exc}", flush=True)
            summary.append({"code": "hainan_appointments", "ok": False, "error": str(exc)})

    dump_json(OUT / "summary.json", summary)

    if args.skip_ingest:
        print(json.dumps({"summary": summary, "ingested": False}, ensure_ascii=False))
        return

    conn = connect(args.db)
    try:
        n_l = ingest_leader_results(leader_payloads, conn=conn) if leader_payloads else 0
        n_e = ingest_appointment_results(appt_payloads, conn=conn) if appt_payloads else 0
        conn.commit()
        touched = set()
        for payload in leader_payloads:
            for leader in payload.get("leaders") or []:
                touched.add((payload["bureau"], leader["person_name"]))
        if touched:
            recompute_persons(conn, touched)
            conn.commit()
    finally:
        conn.close()

    print(
        json.dumps(
            {"summary": summary, "ingested_leaders": n_l, "ingested_events": n_e},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
