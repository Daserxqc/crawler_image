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

from tax_platform.config.city_discovery import (
    discover_city_channel_hubs,
    discover_city_sites_from_html,
    enrich_city_hub_urls,
    CitySiteCandidate,
)
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

    def test_city_channel_hubs_from_footer(self) -> None:
        html = """
        <div class="ft">
          <span>市局频道</span>
          <ul>
            <li><a href="/col/col40/index.html">济南</a></li>
            <li><a href="/col/col41/index.html">淄博</a></li>
            <li><a href="/col/col8424/index.html">南京</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://shandong.chinatax.gov.cn/",
            parent_code="shandong",
            region="山东省",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("shandong_col40", by_code)
        self.assertEqual(by_code["shandong_col40"].name, "国家税务总局济南市税务局")
        self.assertIn("/col/col40/", by_code["shandong_col40"].home_url)

    def test_enrich_hub_finds_appointment_on_leader_sidebar(self) -> None:
        cand = CitySiteCandidate(
            code="jiangsu_col8424",
            name="国家税务总局南京市税务局",
            level="city",
            parent_code="jiangsu",
            home_url="https://jiangsu.chinatax.gov.cn/col/col8424/index.html",
            notes=["city_channel_hub"],
        )
        html = """
        <ul>
          <li><a href="/col/col9157/index.html">领导简介</a></li>
          <li><a href="/col/col9174/index.html">人事任免</a></li>
        </ul>
        """
        enrich_city_hub_urls(html, cand.home_url, cand)
        self.assertIn("col9174", cand.appointment_list_url or "")
        self.assertIn("col9157", cand.leader_intro_url or "")

    def test_detect_appointment_list_by_meta_and_body(self) -> None:
        from tax_platform.config.city_discovery import _page_looks_like_appointment_list

        html = """
        <html><head>
          <meta name="ColumnName" content="人事任免">
          <title>淄博市 人事任免</title>
        </head><body>
          <a>国家税务总局淄博市税务局任免工作人员（2023年12月15日）</a>
          <a>国家税务总局淄博市税务局任免工作人员（2023年12月1日）</a>
        </body></html>
        """
        self.assertTrue(_page_looks_like_appointment_list(html))

    def test_parse_xxgk_tree_funclick_labels(self) -> None:
        from tax_platform.crawler.xxgk_list import parse_xxgk_tree_labels, resolve_xxgk_infotype

        tree = r"""
        d.add(4,3,0,'<a style="cursor:hand;" onclick="funclick(\'A0101\',\'createdatetime:0,orderid:0\');">领导简介</a>');
        d.add(221,101,0,'<a style="cursor:hand;" onclick="funclick(\'A2001\',\'createdatetime:0,orderid:0\');">人事任免</a>');
        """
        labels = parse_xxgk_tree_labels(tree)
        self.assertEqual(labels.get("人事任免"), "A2001")
        self.assertEqual(labels.get("领导简介"), "A0101")
        self.assertEqual(resolve_xxgk_infotype(labels, want="appointment"), "A2001")

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

    def test_leader_only_registry_entry(self) -> None:
        site = entry_to_bureau_site(
            {
                "code": "shandong_col40",
                "name": "国家税务总局济南市税务局",
                "level": "city",
                "parent_code": "shandong",
                "home_url": "https://shandong.chinatax.gov.cn/col/col40/index.html",
                "appointment_list_url": None,
                "leader_intro_url": "https://shandong.chinatax.gov.cn/col/col10787/index.html",
                "region": "山东省",
            }
        )
        self.assertIsNotNone(site)
        assert site is not None
        self.assertEqual(site.leader_intro_url, "https://shandong.chinatax.gov.cn/col/col10787/index.html")
        self.assertEqual(site.appointment_list_url, "")


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
