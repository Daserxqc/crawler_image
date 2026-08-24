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
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.models.entities import to_dict


def crawl_site(code: str, *, delay: float) -> dict:
    site = get_site(code)
    session = create_session()
    hub_url, hub_html = fetch_html(session, site.leader_intro_url, follow_meta_refresh=True)
    pages = [hub_url]
    if not parse_leader_intro(hub_html, hub_url, site.code):
        pages = leader_page_targets(hub_html, hub_url) or [hub_url]
    duties = []
    failed = []
    html_by_url = {hub_url: hub_html}
    for index, page_url in enumerate(pages):
        if delay and index:
            time.sleep(delay)
        try:
            if page_url not in html_by_url:
                page_url, html = fetch_html(session, page_url, referer=hub_url, follow_meta_refresh=True)
            else:
                html = html_by_url[page_url]
            duties.extend(parse_leader_intro(html, page_url, site.code))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": page_url, "error": str(exc)})
            logging.warning("Failed %s: %s", page_url, exc)
    return {
        "bureau": site.code,
        "hub_url": hub_url,
        "page_count": len(pages),
        "leaders": [to_dict(duty) for duty in duties],
        "failed": failed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public leader introduction pages.")
    parser.add_argument("--site", default="pdtax", help="Bureau code, e.g. pdtax / shanghai")
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--output", default="output/leader_crawl.json")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if args.site == "all":
        payload = [crawl_site(site.code, delay=args.delay) for site in SHANGHAI_SITES]
    else:
        payload = crawl_site(args.site, delay=args.delay)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote %s", out.resolve())


if __name__ == "__main__":
    main()
