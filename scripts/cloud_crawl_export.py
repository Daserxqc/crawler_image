# -*- coding: utf-8 -*-
"""云主机：用本机同步来的 known_urls 做增量爬取，只写出 JSON，不入库。

前置：本机已执行 cloud_sync_export.py，并把 to_cloud/ 同步到本机目录，例如::

    output/cloud_sync/from_local/known_urls.json

然后在云上::

    python scripts/cloud_crawl_export.py

产出 ``output/cloud_sync/to_local/appointments.json``（拉回本机入库）。

把结果拷回本机示例::

    scp -r output/cloud_sync/to_local user@your-pc:E:/crawler_image/output/cloud_sync/from_cloud/
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_job import appointments_payload, crawl_appointments
from tax_platform.crawler.crawl_state import DEFAULT_STATE_PATH
from tax_platform.crawler.job_io import dump_json

FROM_LOCAL = ROOT / "output" / "cloud_sync" / "from_local"
TO_LOCAL = ROOT / "output" / "cloud_sync" / "to_local"


def _load_known_urls(path: Path) -> set[str]:
    if not path.is_file():
        raise SystemExit(f"缺少 known_urls 文件: {path}（请先从本机 sync export 再上传）")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        urls = data.get("urls") or []
    elif isinstance(data, list):
        urls = data
    else:
        raise SystemExit(f"无法解析 known_urls: {path}")
    return {str(u).strip() for u in urls if str(u).strip()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--known-urls",
        type=Path,
        default=FROM_LOCAL / "known_urls.json",
    )
    parser.add_argument(
        "--state-in",
        type=Path,
        default=FROM_LOCAL / "crawl_state.json",
        help="可选：用本机带来的 crawl_state 覆盖云上状态后再爬",
    )
    parser.add_argument("--out-dir", type=Path, default=TO_LOCAL)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--level", default=None)
    parser.add_argument("--site", default="all")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略到期窗口（首次云爬或排障时可用）",
    )
    args = parser.parse_args()

    known = _load_known_urls(args.known_urls)
    logging.info("Loaded %s known notice URLs", len(known))

    if args.state_in.is_file():
        DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.state_in, DEFAULT_STATE_PATH)
        logging.info("Restored crawl_state from %s", args.state_in)

    result = crawl_appointments(
        args.site,
        limit=args.limit,
        delay=args.delay,
        due_only=not args.force,
        level=args.level,
        incremental=True,
        known_urls=known,
        record_state=True,
    )
    payload = appointments_payload(result)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamped = out_dir / f"appointments_{stamp}.json"
    latest = out_dir / "appointments.json"
    dump_json(stamped, payload)
    dump_json(latest, payload)

    # Also ship updated crawl_state back so local due windows stay consistent.
    if DEFAULT_STATE_PATH.is_file():
        shutil.copy2(DEFAULT_STATE_PATH, out_dir / "crawl_state.json")

    rows = payload if isinstance(payload, list) else [payload]
    summary = {
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "sites": len(rows),
        "notices": sum(len(i.get("notices") or []) for i in rows),
        "events": sum(len(i.get("events") or []) for i in rows),
        "skipped_known": sum(int(i.get("skipped") or 0) for i in rows),
        "failed": sum(len(i.get("failed") or []) for i in rows),
        "known_urls_used": len(known),
        "latest": str(latest.resolve()),
        "stamped": str(stamped.resolve()),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
