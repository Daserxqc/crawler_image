from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.fujian_was5 import (
    _docs_from_was5,
    _extract_channelid,
    _extract_rs_chnlid_and_file,
    fetch_fujian_was5_list_html,
)


SHELL = """
<script>var channelid = 203958;</script>
<script>
var zNodes = [
  { id: "20235", pId: "0", name: "主动公开基本目录", file: "http://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/jgsz/" },
  { id: "20245", pId: "20235", name: "人事任免", t: "人事任免",
    file: "http://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/rsxx_1009/", c: "167" }
];
</script>
"""

WAS5_BODY = """{"count":"2","chnlname":"福建国税",
"pagenum":"1",
"searchtime":"0.01",
"py":'###pypy###',
"dy":'###dydy###',
"docs":[
{
"title":"国家税务总局福州市税务局任免工作人员（2026年3月6日）",
"title2":"国家税务总局福州市税务局任免工作人员（2026年3月6日）",
"url":"http://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/rsxx_1009/202605/t20260507_634732.htm",
"chnldocurl":"http://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/rsxx_1009/202605/t20260507_634732.htm",
"time":"2026-05-07","docid":"634732"
}
]}
"""


class FujianWas5Tests(unittest.TestCase):
    def test_extracts_ids_from_shell(self) -> None:
        self.assertEqual(_extract_channelid(SHELL), "203958")
        chnlid, file_url = _extract_rs_chnlid_and_file(SHELL)
        self.assertEqual(chnlid, "20245")
        self.assertIn("rsxx_1009", file_url or "")

    def test_parses_was5_quirky_json(self) -> None:
        docs = _docs_from_was5(WAS5_BODY)
        self.assertEqual(len(docs), 1)
        self.assertIn("任免工作人员", docs[0]["title"])
        self.assertEqual(docs[0]["time"], "2026-05-07")

    def test_synthetic_html_parses_with_file_date(self) -> None:
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = WAS5_BODY
        session.get.return_value = resp
        url = "https://fujian.chinatax.gov.cn/fzsswj/zfxxgkzl/zfxxgkml/jgsz/"
        html = fetch_fujian_was5_list_html(session, url, SHELL)
        self.assertIsNotNone(html)
        items = parse_appointment_list(html or "", url)
        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].published_on), "2026-05-07")
        self.assertTrue(items[0].source_url.endswith("t20260507_634732.htm"))


if __name__ == "__main__":
    unittest.main()
