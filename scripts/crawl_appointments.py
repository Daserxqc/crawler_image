from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites_shanghai import SHANGHAI_SITES, get_site
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.models.entities import to_dict


def crawl_site(code: str, *, limit: int, delay: float) -> dict:
    site = get_site(code)
    session = create_session()
    list_url, list_html = fetch_html(session, site.appointment_list_url, follow_meta_refresh=False)
    items = parse_appointment_list(list_html, list_url)[:limit]
    notices = []
    events = []
    failed = []
    for item in items:
        time.sleep(delay)
        try:
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
            )
            notice = parse_appointment_detail(detail_html, detail_url, site.code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)})
            logging.warning("Failed %s: %s", item.source_url, exc)
    return {
        "bureau": site.code,
        "list_url": list_url,
        "list_count": len(items),
        "notices": [to_dict(notice) for notice in notices],
        "events": [to_dict(event) for event in events],
        "failed": failed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public appointment notices.")
    parser.add_argument("--site", default="pdtax", help="Bureau code, e.g. pdtax / shanghai / hptax")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--output", default="output/appointment_crawl.json")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if args.site == "all":
        payload = [crawl_site(site.code, limit=args.limit, delay=args.delay) for site in SHANGHAI_SITES]
    else:
        payload = crawl_site(args.site, limit=args.limit, delay=args.delay)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote %s", out.resolve())


if __name__ == "__main__":
    main()
