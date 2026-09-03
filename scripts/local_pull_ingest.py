# -*- coding: utf-8 -*-
"""本机：拉取云上爬到的 appointments.json 并入库（网站/账号数据仍在本机）。

用法::

    # 先把云上 output/cloud_sync/to_local/ 同步到本机 from_cloud/
    python scripts/local_pull_ingest.py --db output/tax_hr.db

默认读取 ``output/cloud_sync/from_cloud/appointments.json``。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.crawl_state import DEFAULT_STATE_PATH
from tax_platform.store.ingest import ingest_appointment_results
from tax_platform.store.schema import connect

FROM_CLOUD = ROOT / "output" / "cloud_sync" / "from_cloud"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument(
        "--appointments",
        type=Path,
        default=FROM_CLOUD / "appointments.json",
    )
    parser.add_argument(
        "--state-in",
        type=Path,
        default=FROM_CLOUD / "crawl_state.json",
        help="若云端带回了 crawl_state，合并覆盖到本机",
    )
    parser.add_argument(
        "--rebuild-posts",
        action="store_true",
        help="入库后重建岗位任期表（较慢）",
    )
    args = parser.parse_args()

    path: Path = args.appointments
    if not path.is_file():
        raise SystemExit(f"找不到云爬结果: {path}（请先从云主机同步 to_local/）")

    payload = json.loads(path.read_text(encoding="utf-8"))
    conn = connect(args.db)
    try:
        ingested = ingest_appointment_results(payload, conn=conn)
        conn.commit()
    finally:
        conn.close()
    logging.info("Ingested events: %s", ingested)

    if args.state_in.is_file():
        DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.state_in, DEFAULT_STATE_PATH)
        logging.info("Updated local crawl_state from cloud")

    summary: dict = {
        "ingested_events": ingested,
        "source": str(path.resolve()),
        "db": str(Path(args.db).resolve()),
    }

    if args.rebuild_posts:
        from tax_platform.store.identity import sync_person_identities
        from tax_platform.store.posts import rebuild_org_posts

        conn = connect(args.db)
        try:
            sync_person_identities(conn)
            stats = rebuild_org_posts(conn)
            conn.commit()
            summary["posts_rebuild"] = stats
        finally:
            conn.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
