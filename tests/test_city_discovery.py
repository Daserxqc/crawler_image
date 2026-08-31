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

    def test_district_channel_hubs_chongqing_style(self) -> None:
        html = """
        <div class="footer">
          <span>区县频道</span>
          <ul>
            <li><a href="/qxtax/wz/">万州区</a></li>
            <li><a href="/qxtax/yz/">渝中区</a></li>
            <li><a href="/qxtax/qj/">黔江区</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://chongqing.chinatax.gov.cn/cqtax/",
            parent_code="chongqing",
            region="重庆市",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("chongqing_qxtax_wz", by_code)
        self.assertEqual(by_code["chongqing_qxtax_wz"].level, "district")
        self.assertIn("/qxtax/wz/", by_code["chongqing_qxtax_wz"].home_url)

    def test_city_channel_hubs_zhejiang_slug_paths(self) -> None:
        html = """
        <div class="footer">
          <span>市局频道</span>
          <ul>
            <li><a href="/hangzhou/index.html">杭州市</a></li>
            <li><a href="/wenzhou/index.html">温州市</a></li>
            <li><a href="/shaoxing/index.html">绍兴市</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://zhejiang.chinatax.gov.cn/",
            parent_code="zhejiang",
            region="浙江省",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("zhejiang_path_hangzhou", by_code)
        self.assertEqual(by_code["zhejiang_path_hangzhou"].name, "国家税务总局杭州市税务局")

    def test_city_channel_hubs_guangdong_gdsw_paths(self) -> None:
        html = """
        <div class="footer">
          <span>市局频道</span>
          <ul>
            <li><a href="/gdsw/gzsw/gzsw_index.shtml">广州</a></li>
            <li><a href="/gdsw/zhsw/zhsw_index.shtml">珠海</a></li>
            <li><a href="/gdsw/stsw/stsw_index.shtml">汕头</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://guangdong.chinatax.gov.cn/gdsw/index.shtml",
            parent_code="guangdong",
            region="广东省",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("guangdong_gd_gzsw", by_code)
        self.assertIn("/gdsw/gzsw/", by_code["guangdong_gd_gzsw"].home_url)

    def test_city_channel_hubs_fujian_sswj_paths(self) -> None:
        html = """
        <div class="footer">
          <span>市局频道</span>
          <ul>
            <li><a href="/fzsswj/">福州</a></li>
            <li><a href="/zzsswj/">漳州</a></li>
            <li><a href="/qzsswj/">泉州</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://fujian.chinatax.gov.cn/",
            parent_code="fujian",
            region="福建省",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("fujian_fj_fzsswj", by_code)
        self.assertIn("/fzsswj/", by_code["fujian_fj_fzsswj"].home_url)

    def test_city_channel_hubs_hubei_script_labels(self) -> None:
        html = """
        <div class="footer">
          <span>市州频道</span>
          <ul>
            <li><a href="/hbsw/wuhan/index.html" target="_blank">
              <script type="text/javascript">
              document.write('武汉市税务局'.split("市税务局")[0].split("省税务局")[0].split("税务局")[0])
              </script>
            </a></li>
            <li><a href="/hbsw/xiangyang/index.html" target="_blank">
              <script type="text/javascript">
              document.write('襄阳市税务局'.split("市税务局")[0].split("省税务局")[0].split("税务局")[0])
              </script>
            </a></li>
            <li><a href="/hbsw/yichang/index.html" target="_blank">
              <script type="text/javascript">
              document.write('宜昌市税务局'.split("市税务局")[0].split("省税务局")[0].split("税务局")[0])
              </script>
            </a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "http://hubei.chinatax.gov.cn/",
            parent_code="hubei",
            region="湖北省",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("hubei_hbsw_wuhan", by_code)
        self.assertEqual(by_code["hubei_hbsw_wuhan"].name, "国家税务总局武汉市税务局")
        self.assertIn("/hbsw/wuhan/", by_code["hubei_hbsw_wuhan"].home_url)

    def test_hubei_hbsw_xxgk_guess_urls(self) -> None:
        from tax_platform.config.city_discovery import _guess_hbsw_xxgk_urls

        appt, leader = _guess_hbsw_xxgk_urls(
            "http://hubei.chinatax.gov.cn/hbsw/wuhan/index.html"
        )
        self.assertEqual(
            appt,
            "http://hubei.chinatax.gov.cn/hbsw/wuhan/xxgk/rsrm/index.html",
        )
        self.assertEqual(
            leader,
            "http://hubei.chinatax.gov.cn/hbsw/wuhan/xxgk/ldjj/index.html",
        )

    def test_accept_hbsw_appointment_rejects_xxgk_index(self) -> None:
        from tax_platform.config.city_discovery import _accept_as_appointment_url

        cand = CitySiteCandidate(
            code="hubei_hbsw_wuhan",
            name="国家税务总局武汉市税务局",
            level="city",
            parent_code="hubei",
            home_url="http://hubei.chinatax.gov.cn/hbsw/wuhan/index.html",
        )
        self.assertFalse(
            _accept_as_appointment_url(
                "http://hubei.chinatax.gov.cn/hbsw/wuhan/xxgk/index.html",
                cand,
            )
        )
        self.assertTrue(
            _accept_as_appointment_url(
                "http://hubei.chinatax.gov.cn/hbsw/wuhan/xxgk/rsrm/index.html",
                cand,
            )
        )

    def test_resolve_qxtax_zwgk_appointment_url(self) -> None:
        from tax_platform.config import city_discovery as cd

        cand = CitySiteCandidate(
            code="chongqing_qxtax_wz",
            name="国家税务总局万州区税务局",
            level="district",
            parent_code="chongqing",
            home_url="https://chongqing.chinatax.gov.cn/qxtax/wz/",
            notes=["city_channel_hub"],
        )
        with mock.patch.object(
            cd,
            "_load_qxtax_fbfl_map",
            return_value={"wz": 7915},
        ):
            cd._resolve_qxtax_zwgk_urls(mock.Mock(), cand, delay=0)
        self.assertEqual(
            cand.appointment_list_url,
            "https://chongqing.chinatax.gov.cn/qxtax/wz/zwgk/index.html?fbfldm=7916",
        )
        self.assertIn("appointment_qxtax_zwgk:7916", cand.notes)

    def test_qxtax_zwgk_shell_detection(self) -> None:
        from tax_platform.config.city_discovery import _page_looks_like_qxtax_zwgk_shell

        html = """
        <html><head><script src="../../images/zwgkml.js"></script></head>
        <body><span class="flnode newfldm">人事任免</span></body></html>
        """
        self.assertTrue(_page_looks_like_qxtax_zwgk_shell(html))

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

    def test_tianjin_district_channel_hubs(self) -> None:
        html = """
        <div id="sjpd">
          <span title="区局频道">区局频道</span>
          <table>
            <tr>
              <td><a href="/11241000000/index.jsp" title="和平区">和平区</a></td>
              <td><a href="/11242000000/index.jsp" title="河东区">河东区</a></td>
              <td><a href="/11243000000/index.jsp" title="河西区">河西区</a></td>
            </tr>
          </table>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "https://tianjin.chinatax.gov.cn/",
            parent_code="tianjin",
            region="天津市",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("tianjin_fjdm_11241000000", by_code)
        self.assertIn("tianjin_fjdm_11242000000", by_code)
        self.assertEqual(
            by_code["tianjin_fjdm_11241000000"].home_url,
            "https://tianjin.chinatax.gov.cn/11241000000/index.jsp",
        )
        self.assertIn(
            "u_zlmViewMx.action?fjdm=11241000000&lmdm=01000501",
            by_code["tianjin_fjdm_11241000000"].appointment_list_url or "",
        )
        self.assertIn(
            "lmdm=010002",
            by_code["tianjin_fjdm_11241000000"].leader_intro_url or "",
        )

    def test_xinjiang_prefecture_channel_hubs(self) -> None:
        html = """
        <div class="footer">
          <span>地州频道</span>
          <ul>
            <li><a href="/ylz/">伊犁</a></li>
            <li><a href="/htdq/">和田</a></li>
            <li><a href="/ksdq/">喀什</a></li>
            <li><a href="/wlmq/">乌鲁木齐</a></li>
          </ul>
        </div>
        """
        hubs = discover_city_channel_hubs(
            html,
            "http://xinjiang.chinatax.gov.cn/",
            parent_code="xinjiang",
            region="新疆维吾尔自治区",
        )
        by_code = {c.code: c for c in hubs}
        self.assertIn("xinjiang_path_ylz", by_code)
        self.assertIn("xinjiang_path_htdq", by_code)
        self.assertIn("/ylz/", by_code["xinjiang_path_ylz"].home_url)

    def test_xinjiang_xxgk_appt_paths_from_html(self) -> None:
        html = """
        <ul>
          <li><a href="/ylz/ylzxxgk/ylz_28558/fdzdgknr/zsjs/rsrm_22397/">人事任免</a></li>
          <li><a href="/htdq/xxgk/htdq_31801/fdzdgknr/zsjs/rsrm_22397/">和田人事任免</a></li>
          <li><a href="/ylz/ylzxxgk/ldjj/">领导简介</a></li>
        </ul>
        """
        found = discover_city_sites_from_html(
            html,
            "http://xinjiang.chinatax.gov.cn/",
            parent_code="xinjiang",
            region="新疆维吾尔自治区",
        )
        by_code = {c.code: c for c in found}
        self.assertIn("xinjiang_xj_ylz", by_code)
        self.assertIn("xinjiang_xj_htdq", by_code)
        self.assertEqual(
            by_code["xinjiang_xj_ylz"].appointment_list_url,
            "http://xinjiang.chinatax.gov.cn/ylz/ylzxxgk/ylz_28558/fdzdgknr/zsjs/rsrm_22397/",
        )
        self.assertEqual(
            by_code["xinjiang_xj_htdq"].appointment_list_url,
            "http://xinjiang.chinatax.gov.cn/htdq/xxgk/htdq_31801/fdzdgknr/zsjs/rsrm_22397/",
        )
        self.assertIn("/ylz/ylzxxgk/ldjj/", by_code["xinjiang_xj_ylz"].leader_intro_url or "")

    def test_xinjiang_xxgk_seed_urls(self) -> None:
        from tax_platform.config.city_discovery import _xinjiang_xxgk_seeds

        seeds = _xinjiang_xxgk_seeds("http://xinjiang.chinatax.gov.cn/ylz/")
        self.assertIn("http://xinjiang.chinatax.gov.cn/ylz/ylzxxgk/", seeds)
        self.assertIn("http://xinjiang.chinatax.gov.cn/ylz/xxgk/", seeds)

    def test_xinjiang_prefecture_fallback_catalog(self) -> None:
        from tax_platform.config.city_discovery import discover_xinjiang_prefecture_fallback

        found = discover_xinjiang_prefecture_fallback(
            "xinjiang",
            region="新疆维吾尔自治区",
        )
        by_code = {c.code: c for c in found}
        self.assertIn("xinjiang_xj_ylz", by_code)
        self.assertIn("xinjiang_xj_htdq", by_code)
        self.assertEqual(
            by_code["xinjiang_xj_ylz"].appointment_list_url,
            "http://xinjiang.chinatax.gov.cn/ylz/ylzxxgk/ylz_28558/fdzdgknr/zsjs/rsrm_22397/",
        )
        self.assertEqual(
            by_code["xinjiang_xj_htdq"].appointment_list_url,
            "http://xinjiang.chinatax.gov.cn/htdq/xxgk/htdq_31801/fdzdgknr/zsjs/rsrm_22397/",
        )
        self.assertIn("xinjiang_prefecture_fallback", by_code["xinjiang_xj_ylz"].notes)

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
