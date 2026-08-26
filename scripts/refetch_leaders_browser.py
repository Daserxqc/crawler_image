"""Re-fetch leader pages with platform fetch_html (Playwright/WAF-capable)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import get_site
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.models.entities import to_dict
from tax_platform.store import ingest_leader_results
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

DEFAULT_SITES = [
    "chongqing",
    "fujian",
    "gansu",
    "heilongjiang",
    "jiangxi",
    "jilin",
    "ningxia",
    "qinghai",
    "shaanxi",
    "xinjiang",
    "yunnan",
    "sta",
    "henan",
    "hainan",
    "hebei",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", default=",".join(DEFAULT_SITES))
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--max-urls", type=int, default=40)
    args = parser.parse_args()
    codes = [c.strip() for c in args.sites.split(",") if c.strip()]

    session = create_session()
    conn = connect(args.db)
    payloads: list[dict] = []
    stats = []
    try:
        for code in codes:
            site = get_site(code)
            seeds = [site.leader_intro_url]
            for (url,) in conn.execute(
                "SELECT DISTINCT source_url FROM leader_duties WHERE bureau_code=?",
                (code,),
            ):
                if url and url not in seeds:
                    seeds.append(url)

            expanded: list[str] = []
            seen: set[str] = set()
            for seed in seeds[: args.max_urls]:
                if seed in seen:
                    continue
                seen.add(seed)
                expanded.append(seed)
                try:
                    final, html = fetch_html(session, seed, follow_meta_refresh=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"  seed fail {code} {seed}: {exc}", flush=True)
                    continue
                if "chrome-error" in (final or "") or len(html) < 800:
                    print(f"  seed bad {code} len={len(html)} final={final}", flush=True)
                    continue
                for target in leader_page_targets(html, final or seed):
                    if target not in seen:
                        seen.add(target)
                        expanded.append(target)

            expanded = expanded[: args.max_urls]
            print(f"=== {code} urls={len(expanded)} ===", flush=True)
            leaders = []
            failed = 0
            for i, url in enumerate(expanded):
                if args.delay and i:
                    time.sleep(args.delay)
                try:
                    final, html = fetch_html(
                        session, url, referer=site.leader_intro_url, follow_meta_refresh=True
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  fail {url}: {exc}", flush=True)
                    continue
                if "chrome-error" in (final or "") or len(html) < 800:
                    failed += 1
                    continue
                for duty in parse_leader_intro(html, final or url, code):
                    leaders.append(duty)

            with_deps = sum(1 for d in leaders if d.departments_raw)
            print(
                f"  people={len({d.person_name for d in leaders})} "
                f"with_deps={with_deps} failed={failed}",
                flush=True,
            )
            stats.append(
                {
                    "code": code,
                    "urls": len(expanded),
                    "people": len({d.person_name for d in leaders}),
                    "with_deps": with_deps,
                    "failed": failed,
                }
            )
            if leaders:
                payloads.append(
                    {
                        "bureau": code,
                        "hub_url": site.leader_intro_url,
                        "page_count": len(expanded),
                        "leaders": [to_dict(d) for d in leaders],
                        "failed": [],
                    }
                )

        n = 0
        if payloads:
            n = ingest_leader_results(payloads, conn=conn)
            conn.commit()
            touched = {(p["bureau"], d["person_name"]) for p in payloads for d in p["leaders"]}
            recompute_persons(conn, touched)
            conn.commit()
    finally:
        conn.close()

    print(json.dumps({"stats": stats, "ingested": n}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
