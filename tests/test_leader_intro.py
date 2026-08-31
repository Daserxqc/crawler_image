from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.http_client import extract_meta_refresh_url
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro

FIXTURES = ROOT / "tests" / "fixtures"
HUB = "https://shanghai.chinatax.gov.cn/pdtax/xxgk/ldjj/"


class LeaderIntroTests(unittest.TestCase):
    def test_follows_meta_refresh_from_hub(self) -> None:
        html = (FIXTURES / "leader_refresh.html").read_text(encoding="utf-8")
        target = extract_meta_refresh_url(html, HUB)
        self.assertEqual(target, "https://shanghai.chinatax.gov.cn/pdtax/xxgk/ldjj/201811/t442819.html")
        self.assertEqual(leader_page_targets(html, HUB), [target])

    def test_collects_article_links_from_list(self) -> None:
        html = (FIXTURES / "leader_list.html").read_text(encoding="utf-8")
        urls = leader_page_targets(html, HUB)
        self.assertIn("https://shanghai.chinatax.gov.cn/pdtax/xxgk/ldjj/ld_29881/", urls)

    def test_parses_city_leader_page_without_footer_noise(self) -> None:
        html = (FIXTURES / "leader_city_page.html").read_text(encoding="utf-8")
        duties = parse_leader_intro(html, "https://example.test/ldjj/ld_29881/", "shanghai")
        self.assertGreaterEqual(len(duties), 1)
        by_name = {duty.person_name: duty for duty in duties}
        leader = by_name["程俊峰"]
        self.assertEqual(leader.duty_summary, "主持全面工作")
        self.assertEqual(leader.departments_raw, ["国家税务总局上海市浦东新区税务局"])
        joined = "".join(leader.departments_raw)
        self.assertNotIn("网站地图", joined)
        self.assertNotIn("周玉海", joined)

    def test_collects_sidebar_leader_links(self) -> None:
        html = (FIXTURES / "leader_city_page.html").read_text(encoding="utf-8")
        urls = leader_page_targets(html, "https://shanghai.chinatax.gov.cn/xxgk/ldjj/ld_29881/")
        self.assertIn("https://shanghai.chinatax.gov.cn/xxgk/ldjj/ld_29882/", urls)
        self.assertIn("https://shanghai.chinatax.gov.cn/xxgk/ldjj/2022/", urls)

    def test_parses_multi_leader_page_without_ethnicity(self) -> None:
        html = (FIXTURES / "leader_mhtax.html").read_text(encoding="utf-8")
        duties = parse_leader_intro(html, "https://example.test/mhtax/ldjj.html", "mhtax")
        self.assertEqual(len(duties), 3)
        self.assertEqual([duty.person_name for duty in duties], ["张亮", "郁雅芳", "向莹"])
        self.assertEqual(duties[1].departments_raw[0], "财产和行为税科")

    def test_parses_overall_charge_and_oversight(self) -> None:
        html = (FIXTURES / "leader_intro.html").read_text(encoding="utf-8")
        duties = parse_leader_intro(html, "https://example.test/ldjj.html", "pdtax")
        self.assertEqual(len(duties), 2)

        zheng = duties[0]
        self.assertEqual(zheng.person_name, "郑燕")
        self.assertEqual(zheng.gender, "女")
        self.assertEqual(zheng.ethnicity, "汉族")
        self.assertIn("党委书记、局长", zheng.title_raw)
        self.assertEqual(zheng.duty_summary, "主持全面工作")
        self.assertEqual(zheng.departments_raw, [])

        zhang = duties[1]
        self.assertEqual(zhang.person_name, "张继会")
        self.assertEqual(zhang.duty_summary, "分管工作")
        self.assertIn("个人所得税处", zhang.departments_raw)
        self.assertIn("临港税务分局", zhang.departments_raw)
        self.assertIn("第十税务所", zhang.departments_raw)
        self.assertEqual(zhang.departments_raw[-1], "第三十税务所")


    def test_parses_fujian_heading_bio_profiles(self) -> None:
        html = (FIXTURES / "leader_fujian.html").read_text(encoding="utf-8")
        duties = parse_leader_intro(
            html,
            "https://fujian.chinatax.gov.cn/xxgk/ldjj/201810/t20181019_279934.htm",
            "fujian",
        )
        self.assertGreaterEqual(len(duties), 10)
        by_name = {duty.person_name: duty for duty in duties}
        lin = by_name["林京华"]
        self.assertIn("党委书记", lin.title_raw)
        self.assertEqual(lin.gender, "男")
        zhao = by_name["赵静"]
        self.assertEqual(zhao.gender, "女")

    def test_parses_beijing_ld_con_profile(self) -> None:
        html = (FIXTURES / "leader_beijing.html").read_text(encoding="utf-8")
        duties = parse_leader_intro(
            html,
            "http://beijing.chinatax.gov.cn/bjswj/ldxx01/ldjianjie.shtml",
            "beijing",
        )
        self.assertGreaterEqual(len(duties), 8)
        by_name = {duty.person_name: duty for duty in duties}
        self.assertIn("练奇峰", by_name)
        self.assertIn("局长", by_name["练奇峰"].title_raw)
        targets = leader_page_targets(
            html, "http://beijing.chinatax.gov.cn/bjswj/ldxx01/ldjianjie.shtml"
        )
        self.assertGreaterEqual(len(targets), 1)

    def test_parses_hebei_sidebar_leader_list(self) -> None:
        html = (FIXTURES / "leader_hebei.html").read_text(encoding="utf-8")
        hub = "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html"
        duties = parse_leader_intro(html, hub, "hebei")
        self.assertEqual(len(duties), 3)
        self.assertEqual(duties[0].person_name, "赵杰方")
        self.assertIn("党委书记", duties[0].title_raw)
        self.assertEqual(duties[0].duty_summary, "主持全面工作")
        self.assertEqual(duties[0].gender, "男")
        self.assertEqual(duties[0].ethnicity, "汉族")
        self.assertEqual(duties[1].person_name, "靳伟")
        self.assertEqual(duties[1].duty_summary, "分管工作")
        self.assertEqual(duties[2].person_name, "李圆")
        self.assertEqual(duties[2].duty_summary, "分管工作")

    def test_parses_jilin_sidebar_over_single_ldjj2022(self) -> None:
        html = (FIXTURES / "leader_jilin_sidebar.html").read_text(encoding="utf-8")
        hub = "https://jilin.chinatax.gov.cn/col/col24472/index.html"
        duties = parse_leader_intro(html, hub, "jilin")
        self.assertEqual(len(duties), 3)
        self.assertEqual([d.person_name for d in duties], ["王宏伟", "田骅", "于洋"])
        self.assertEqual(duties[0].duty_summary, "主持全面工作")
        self.assertEqual(duties[1].duty_summary, "分管工作")

    def test_parses_heilongjiang_name_title_paren_list(self) -> None:
        html = (FIXTURES / "leader_heilongjiang_list.html").read_text(encoding="utf-8")
        hub = "http://heilongjiang.chinatax.gov.cn/col/col11194/index.html"
        duties = parse_leader_intro(html, hub, "heilongjiang")
        self.assertEqual(len(duties), 3)
        self.assertEqual(duties[0].person_name, "杨鹏")
        self.assertIn("党委书记", duties[0].title_raw)
        self.assertEqual(duties[1].person_name, "刘晓辉")
        self.assertEqual(duties[2].person_name, "宫春河")

    def test_parses_hubei_document_write_leader_list(self) -> None:
        html = (FIXTURES / "leader_hubei_hbsw.html").read_text(encoding="utf-8")
        hub = "http://hubei.chinatax.gov.cn/hbsw/wuhan/xxgk/ldjj/index.html"
        duties = parse_leader_intro(html, hub, "hubei_hbsw_wuhan")
        self.assertEqual(len(duties), 2)
        self.assertEqual(duties[0].person_name, "黄英")
        self.assertEqual(duties[0].gender, "女")
        self.assertEqual(duties[0].ethnicity, "汉族")
        self.assertIn("党委书记", duties[0].title_raw)
        self.assertEqual(duties[0].duty_summary, "主持全面工作")
        self.assertEqual(duties[1].person_name, "肖泽民")
        self.assertEqual(duties[1].duty_summary, "分管工作")
        self.assertIn("政策法规处", duties[1].departments_raw[0])


if __name__ == "__main__":
    unittest.main()
