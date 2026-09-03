from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.sta_chinatax import fetch_sta_list_html


STA_API = """{"code":200,"results":{"data":{"page":1,"rows":1,"total":1,"results":[
{"title":"国家税务总局任免工作人员（2026年8月27日）",
"url":"https://www.chinatax.gov.cn/chinatax/n810214/c102374/c102384/n810611r/art/2026/8/art_20260827.html",
"publishedTimeStr":"2026-08-27"}
]}}}"""


def test_sta_list_html() -> None:
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = __import__("json").loads(STA_API)
    session.post.return_value = resp
    url = "https://www.chinatax.gov.cn/chinatax/n810214/c102374/c102384/n810611r/"
    html = fetch_sta_list_html(session, url)
    assert html is not None
    items = parse_appointment_list(html, url)
    assert len(items) == 1
    assert "任免工作人员" in items[0].title
    assert str(items[0].published_on) == "2026-08-27"
