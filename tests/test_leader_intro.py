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
        self.assertTrue(urls[-1].endswith("/pdtax/xxgk/ldjj/201811/t442819.html"))

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


if __name__ == "__main__":
    unittest.main()
