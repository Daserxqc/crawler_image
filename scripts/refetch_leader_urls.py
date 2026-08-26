"""Re-fetch existing leader_duties source_url pages via curl and upgrade oversight."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.models.entities import to_dict
from tax_platform.store import ingest_leader_results
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
    "sta",
]


def curl_get(url: str, *, timeout: int = 45) -> str:
    cmd = [
        "curl.exe",
        "-sL",
        "--max-time",
        str(timeout),
        "-k",
        "-A",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "--compressed",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"curl {proc.returncode}")
    raw = proc.stdout
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def empty_deps(raw) -> bool:
    if raw is None or not str(raw).strip() or str(raw).strip() in {"[]", "null"}:
        return True
    try:
        return not json.loads(raw)
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", default=",".join(EMPTY_OVERSIGHT))
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    args = parser.parse_args()
    codes = [c.strip() for c in args.sites.split(",") if c.strip()]

    conn = connect(args.db)
    payloads: list[dict] = []
    stats = []
    try:
        for code in codes:
            rows = conn.execute(
                """
                SELECT person_name, title_raw, duty_summary, departments_json, source_url
                FROM leader_duties WHERE bureau_code=?
                """,
                (code,),
            ).fetchall()
            # Unique URLs (hub pages may be shared).
            urls = []
            seen = set()
            for r in rows:
                url = (r["source_url"] or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                urls.append(url)
            upgraded = 0
            leaders = []
            failed = 0
            print(f"=== {code}: {len(rows)} rows, {len(urls)} seed urls ===", flush=True)
            # Expand hubs → detail targets when possible.
            expanded: list[str] = []
            seen_exp: set[str] = set()
            for url in urls:
                if url in seen_exp:
                    continue
                seen_exp.add(url)
                expanded.append(url)
                try:
                    html = curl_get(url)
                except Exception:
                    continue
                for target in leader_page_targets(html, url):
                    if target not in seen_exp:
                        seen_exp.add(target)
                        expanded.append(target)
            print(f"  expanded_urls={len(expanded)}", flush=True)

            upgraded = 0
            leaders = []
            failed = 0
            for i, url in enumerate(expanded):
                if args.delay and i:
                    time.sleep(args.delay)
                try:
                    html = curl_get(url)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  fail {url}: {exc}", flush=True)
                    continue
                if len(html) < 500 or "chrome-error" in html:
                    failed += 1
                    continue
                duties = parse_leader_intro(html, url, code)
                for d in duties:
                    if d.departments_raw:
                        upgraded += 1
                    leaders.append(d)
            if leaders:
                payloads.append(
                    {
                        "bureau": code,
                        "hub_url": get_site_url(code),
                        "page_count": len(urls),
                        "leaders": [to_dict(d) for d in leaders],
                        "failed": [],
                    }
                )
            stats.append(
                {
                    "code": code,
                    "urls": len(urls),
                    "parsed_people": len({d.person_name for d in leaders}),
                    "with_deps": sum(1 for d in leaders if d.departments_raw),
                    "failed": failed,
                }
            )
            print(
                f"  parsed={len(leaders)} with_deps={stats[-1]['with_deps']} failed={failed}",
                flush=True,
            )

        if payloads:
            n = ingest_leader_results(payloads, conn=conn)
            conn.commit()
            touched = {(p["bureau"], d["person_name"]) for p in payloads for d in p["leaders"]}
            recompute_persons(conn, touched)
            conn.commit()
        else:
            n = 0
    finally:
        conn.close()

    print(json.dumps({"stats": stats, "ingested": n}, ensure_ascii=False), flush=True)


def get_site_url(code: str) -> str:
    try:
        from tax_platform.config.sites import get_site

        return get_site(code).leader_intro_url
    except Exception:
        return code


if __name__ == "__main__":
    main()
