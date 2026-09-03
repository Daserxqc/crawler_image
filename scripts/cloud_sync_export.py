# -*- coding: utf-8 -*-
"""本机：导出云爬所需的「已知公告 URL」包，上传到云主机。

用法::

    python scripts/cloud_sync_export.py --db output/tax_hr.db

生成目录 ``output/cloud_sync/to_cloud/``：
  - known_urls.json
  - crawl_state.json（若存在则复制）
  - manifest.json

上传示例（换成你的云主机）::

    scp -r output/cloud_sync/to_cloud user@your-vps:/opt/crawler_image/output/cloud_sync/from_local/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect

DEFAULT_OUT = ROOT / "output" / "cloud_sync" / "to_cloud"
STATE_SRC = ROOT / "output" / "crawl_state.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    conn = connect(args.db)
    try:
        urls = sorted(known_notice_urls(conn=conn))
    finally:
        conn.close()

    urls_path = out / "known_urls.json"
    urls_path.write_text(
        json.dumps({"urls": urls, "count": len(urls)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    state_dst = out / "crawl_state.json"
    if STATE_SRC.is_file():
        shutil.copy2(STATE_SRC, state_dst)
        state_copied = True
    else:
        state_copied = False

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "known_urls": len(urls),
        "crawl_state_copied": state_copied,
        "db": str(Path(args.db).resolve()),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({**manifest, "out": str(out.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
