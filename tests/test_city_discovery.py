from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_discovery import discover_city_sites_from_html
from tax_platform.config.city_sites_io import entry_to_bureau_site, save_city_registry
from tax_platform.config import sites as sites_mod
from tax_platform.crawler.appointment_job import crawl_appointments_site


class CityDiscoveryTests(unittest.TestCase):
    def test_shanghai_style_paths_from_html(self) -> None:
        html = """
        <a href="/pdtax/xxgk/rsrm/">浦东新区税务局人事任免</a>
        <a href="/hptax/xxgk/ldjj/">黄浦区税务局领导简介</a>
        """
        found = discover_city_sites_from_html(
            html,
            "https://shanghai.chinatax.gov.cn/xxgk/",
            parent_code="shanghai",
            region="上海市",
        )
        by_code = {c.code: c for c in found}
        self.assertIn("pdtax", by_code)
        self.assertIn("hptax", by_code)
        self.assertEqual(by_code["pdtax"].level, "district")
        self.assertIn("/xxgk/rsrm/", by_code["pdtax"].appointment_list_url or "")

    def test_registry_merges_into_all_sites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "city_sites_registry.json"
            save_city_registry(
                [
                    {
                        "code": "demo_city",
                        "name": "示范市税务局",
                        "level": "city",
                        "parent_code": "zhejiang",
                        "home_url": "https://zhejiang.chinatax.gov.cn/democity/",
                        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/democity/xxgk/rsrm/",
                        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/democity/xxgk/ldjj/",
                        "region": "浙江省",
                    }
                ],
                path=path,
            )
            with mock.patch("tax_platform.config.sites.load_registry_sites") as load:
                load.return_value = [
                    entry_to_bureau_site(json.loads(path.read_text(encoding="utf-8"))[0])
                ]
                rebuilt = sites_mod._build_all_sites()
            codes = {s.code for s in rebuilt}
            self.assertIn("demo_city", codes)
            self.assertGreaterEqual(len(rebuilt), 49)


class IncrementalCrawlTests(unittest.TestCase):
    def test_known_urls_are_skipped(self) -> None:
        known = {"https://example.com/already"}

        class Item:
            source_url = "https://example.com/already"

        with mock.patch(
            "tax_platform.crawler.appointment_job.get_site"
        ) as get_site, mock.patch(
            "tax_platform.crawler.appointment_job.create_session"
        ), mock.patch(
            "tax_platform.crawler.appointment_job._load_appointment_list_html",
            return_value=("https://example.com/list/", "<html/>"),
        ), mock.patch(
            "tax_platform.crawler.appointment_job.parse_appointment_list",
            return_value=[Item()],
        ), mock.patch(
            "tax_platform.crawler.appointment_job.fetch_html"
        ) as fetch:
            get_site.return_value = mock.Mock(
                code="pdtax",
                appointment_list_url="https://example.com/list/",
            )
            result = crawl_appointments_site(
                "pdtax",
                known_urls=known,
                incremental=True,
                delay=0,
            )
            fetch.assert_not_called()
            self.assertEqual(result.skipped, 1)
            self.assertEqual(result.notices, [])


if __name__ == "__main__":
    unittest.main()
