"""Smoke checks for PR1 foundation (site catalog + HTTP helpers)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites_shanghai import SHANGHAI_SITES, list_sites
from tax_platform.crawler.http_client import (
    create_session,
    ensure_trailing_slash,
    fetch_html,
    resolve_list_child_url,
)


def main() -> None:
    assert len(SHANGHAI_SITES) == 17
    assert len(list_sites("district")) == 16
    assert len(list_sites("province")) == 1
    assert SHANGHAI_SITES[0].code == "shanghai"

    list_url = "https://shanghai.chinatax.gov.cn/pdtax/xxgk/rsrm"
    child = resolve_list_child_url(list_url, "./202606/t480619.html")
    assert child.endswith("/pdtax/xxgk/rsrm/202606/t480619.html"), child
    assert ensure_trailing_slash(list_url).endswith("/")

    session = create_session()
    final_url, html = fetch_html(
        session,
        "https://shanghai.chinatax.gov.cn/pdtax/xxgk/ldjj/",
        follow_meta_refresh=True,
    )
    assert "郑燕" in html or "领导简介" in html, final_url
    assert "t442819" in final_url or "ldjj" in final_url

    payload = {
        "sites": len(SHANGHAI_SITES),
        "leader_final_url": final_url,
        "sample_detail_url": child,
        "leader_html_bytes": len(html),
    }
    out = Path("output/foundation_smoke.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
