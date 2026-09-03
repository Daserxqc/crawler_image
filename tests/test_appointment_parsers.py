from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events, split_post
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import is_appointment_list_url, parse_appointment_list

FIXTURES = ROOT / "tests" / "fixtures"


class AppointmentListTests(unittest.TestCase):
    def test_keeps_appointment_links_and_skips_recruiting(self) -> None:
        html = (FIXTURES / "appointment_list.html").read_text(encoding="utf-8")
        items = parse_appointment_list(html, "https://shanghai.chinatax.gov.cn/pdtax/xxgk/rsrm/")
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].source_url.endswith("/pdtax/xxgk/rsrm/202606/t480619.html"))
        self.assertEqual(str(items[0].published_on), "2026-06-04")
        self.assertNotIn("体检", "".join(item.title for item in items))

    def test_rejects_list_page_urls(self) -> None:
        self.assertTrue(
            is_appointment_list_url("https://shanghai.chinatax.gov.cn/xxgk/rsxx/jgrs/")
        )
        self.assertTrue(
            is_appointment_list_url("https://shanghai.chinatax.gov.cn/pdtax/xxgk/rsrm/")
        )
        self.assertFalse(
            is_appointment_list_url(
                "https://shanghai.chinatax.gov.cn/xxgk/rsxx/202605/t480100.html"
            )
        )

class AppointmentDetailTests(unittest.TestCase):
    def test_strips_br_from_meta_title(self) -> None:
        from tax_platform.crawler.text_clean import normalize_notice_title

        raw = "国家税务总局武汉市税务局任免工作人员<br/>（2026年2月25日）"
        self.assertEqual(
            normalize_notice_title(raw),
            "国家税务总局武汉市税务局任免工作人员",
        )
        dirty = (
            "国家税务总局贵州省税务局任免工作人员 (2024年7月24日) "
            "2024年07月29日 14:58:34 【字体：小 中 大】 打印本页 关闭本页"
        )
        self.assertEqual(
            normalize_notice_title(dirty),
            "国家税务总局贵州省税务局任免工作人员",
        )
        from tax_platform.crawler.text_clean import normalize_doc_no

        self.assertEqual(normalize_doc_no("沪税任〔2026〕117号"), "沪税任〔2026〕117号")
        self.assertIsNone(
            normalize_doc_no(
                "国家税务总局本溪市税务局任免工作人员（2026年8月14日） "
                "字号：[大][中][小]微信扫一扫：分享打印本页正文下载"
            )
        )
        self.assertIsNone(
            normalize_doc_no(
                "发布日期：发文机关：国家税务总局河南省税务局人事处有效性：有效"
                "国家税务总局河南省税务局任免工作人员字号：[大][中][小]"
            )
        )
        html = f"""
        <html><head><meta name="ArticleTitle" content="{raw}"></head>
        <body><div>国家税务总局武汉市税务局决定：任命张三为办公室主任；</div></body></html>
        """
        notice = parse_appointment_detail(html, "https://example.test/wh.html", "wuhan")
        self.assertNotIn("<br", notice.title or "")
        self.assertEqual(notice.title, "国家税务总局武汉市税务局任免工作人员")
        self.assertEqual(str(notice.issued_on), "2026-02-25")

    def test_extracts_meta_and_body(self) -> None:
        html = (FIXTURES / "appointment_detail.html").read_text(encoding="utf-8")
        notice = parse_appointment_detail(
            html,
            "https://example.test/notice.html",
            "pdtax",
        )
        self.assertEqual(notice.doc_no, "沪税浦委任〔2026〕32号")
        self.assertEqual(notice.issuer, "国家税务总局上海市浦东新区税务局")
        self.assertEqual(str(notice.issued_on), "2026-06-04")
        self.assertIn("赵健健任保税区税务分局法制科副科长", notice.raw_text)

    def test_title_decision_date_beats_stale_cms_date(self) -> None:
        """北京等站点标题含任免日，CMS/目录日不可盖过事实日期。"""
        from tax_platform.crawler.appointment_detail import parse_date_from_title, resolve_issued_on

        title = "国家税务总局北京市税务局任免工作人员（2025年12月11日）"
        self.assertEqual(str(parse_date_from_title(title)), "2025-12-11")
        day = resolve_issued_on(
            title=title,
            page_text="发布日期：2025-12-26 发文日期：2026-08-21",
            meta_pubdate="2026-08-21 10:00",
        )
        self.assertEqual(str(day), "2025-12-11")

        html = f"""
        <html><head><meta name="PubDate" content="2026-08-21 10:00"></head>
        <body>
        <h1>{title}</h1>
        <p>发布日期：2025-12-26 13:25</p>
        <p>发文日期：2026-08-21</p>
        <div>国家税务总局北京市税务局决定：任命许亥隆为国家税务总局北京市税务局办公室主任；</div>
        </body></html>
        """
        notice = parse_appointment_detail(html, "https://example.test/bj.html", "beijing")
        self.assertEqual(str(notice.issued_on), "2025-12-11")
        events = extract_appointment_events(notice)
        self.assertTrue(events)
        self.assertEqual(str(events[0].effective_on), "2025-12-11")


class AppointmentClauseTests(unittest.TestCase):
    def test_split_post(self) -> None:
        bureau, department, title = split_post("保税区税务分局法制科副科长")
        self.assertEqual(bureau, "保税区税务分局")
        self.assertEqual(department, "法制科")
        self.assertEqual(title, "副科长")

    def test_split_sta_sizhang(self) -> None:
        bureau, department, title = split_post("国家税务总局人事司司长")
        self.assertEqual(bureau, "国家税务总局")
        self.assertEqual(department, "人事司")
        self.assertEqual(title, "司长")

    def test_extract_appoint_and_dismiss(self) -> None:
        html = (FIXTURES / "appointment_detail.html").read_text(encoding="utf-8")
        notice = parse_appointment_detail(html, "https://example.test/notice.html", "pdtax")
        events = extract_appointment_events(notice)
        names = [event.person_name for event in events]
        self.assertIn("赵健健", names)
        self.assertIn("龚晓栋", names)
        self.assertIn("缪军", names)
        zhao = next(event for event in events if event.person_name == "赵健健")
        self.assertEqual(zhao.action, "appoint")
        self.assertEqual(zhao.department_raw, "法制科")
        self.assertEqual(zhao.probation_years, 1)
        gong = next(event for event in events if event.person_name == "龚晓栋")
        self.assertEqual(gong.action, "dismiss")
        self.assertEqual(gong.title_raw, "副所长")

    def test_hebei_appoint_as_clauses(self) -> None:
        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="hebei",
            title="国家税务总局河北省税务局任免工作人员（2024年12月3日）",
            source_url="http://hebei.chinatax.gov.cn/example.html",
            raw_text=(
                "国家税务总局河北省税务局决定，任命："
                "苗丽晓为国家税务总局河北省税务局党委纪检组副组长（副处长级）；"
                "唐建国为国家税务总局河北省税务局企业所得税处副处长，试用期一年；"
                "韩大伟为国家税务总局河北省税务局第一税务分局（大企业税收服务和管理局）副局长，试用期一年。"
            ),
        )
        events = extract_appointment_events(notice)
        self.assertEqual(len(events), 3)
        self.assertEqual([e.person_name for e in events], ["苗丽晓", "唐建国", "韩大伟"])
        self.assertEqual(events[0].title_raw, "副组长")
        self.assertEqual(events[1].title_raw, "副处长")
        self.assertEqual(events[1].probation_years, 1)
        self.assertEqual(events[2].title_raw, "副局长")
        self.assertNotIn("人事", [e.person_name for e in events])

    def test_appoint_prefix_not_swallowed_into_name(self) -> None:
        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="beijing",
            title="任免",
            source_url="http://beijing.example/n.html",
            raw_text="决定，任命陈双格为政策法规处副处长；任命赵伟为副局长。",
        )
        events = extract_appointment_events(notice)
        self.assertEqual([e.person_name for e in events], ["陈双格", "赵伟"])
        self.assertNotIn("命陈双格", [e.person_name for e in events])

    def test_jiangsu_property_tax_appoint_re(self) -> None:
        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="jiangsu",
            title="任免",
            source_url="http://jiangsu.example/n.html",
            raw_text="秦建平任财产和行为税处处长，试用期一年。",
        )
        events = extract_appointment_events(notice)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].person_name, "秦建平")
        self.assertEqual(events[0].department_raw, "财产和行为税处")
        self.assertEqual(events[0].title_raw, "处长")
        self.assertNotIn("财产和行", [e.person_name for e in events])

    def test_rejects_renmian_title_false_positive(self) -> None:
        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="guangdong",
            title="任免工作人员",
            source_url="http://gd.example/n.html",
            raw_text=(
                "国家税务总局广东省税务局任免工作人员（2025年3月）"
                "国家税务总局广东省税务局决定，任命："
                "陈杰为国家税务总局广东省税务局办公室主任；"
                "申深为国家税务总局广东省税务局人事处处长。"
            ),
        )
        events = extract_appointment_events(notice)
        names = [e.person_name for e in events]
        self.assertEqual(names, ["陈杰", "申深"])
        self.assertNotIn("省税务局", names)
        self.assertNotIn("人事", names)

    def test_split_concurrent_rank_titles(self) -> None:
        bureau, department, title = split_post(
            "国家税务总局新乡市税务局副局长、二级高级主办"
        )
        self.assertEqual(bureau, "国家税务总局新乡市税务局")
        self.assertIsNone(department)
        self.assertEqual(title, "副局长、二级高级主办")

        bureau2, dept2, title2 = split_post(
            "国家税务总局北京市朝阳区税务局党委委员、纪检组组长、三级高级主办"
        )
        self.assertEqual(bureau2, "国家税务总局北京市朝阳区税务局")
        self.assertEqual(title2, "纪检组组长、三级高级主办")
        self.assertEqual(dept2, "党委委员")

        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="henan",
            title="任免",
            source_url="http://henan.example/n.html",
            raw_text="决定，任命：郭天永为国家税务总局新乡市税务局副局长、二级高级主办。",
        )
        events = extract_appointment_events(notice)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].person_name, "郭天永")
        self.assertEqual(events[0].title_raw, "副局长、二级高级主办")
        self.assertTrue(events[0].title_raw)


    def test_dismiss_with_tongzhi_suffix(self) -> None:
        from tax_platform.models.entities import NoticeMeta

        notice = NoticeMeta(
            bureau_code="beijing_yanqing",
            title="国家税务总局北京市延庆区税务局任免工作人员（2021年7月13日）",
            source_url="http://beijing.example/n.html",
            raw_text="决定： 免去田淑芳同志税收经济分析科副科长（正科长级） （主持工作）职务。",
        )
        events = extract_appointment_events(notice)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].person_name, "田淑芳")
        self.assertEqual(events[0].action, "dismiss")

    def test_dismiss_glued_name_post_no_de(self) -> None:
        """辽阳等：免去许绍华国家税务总局…副局长职务（无「的/同志」）。"""
        from tax_platform.models.entities import NoticeMeta
        from tax_platform.normalize.person import is_plausible_person_name

        self.assertFalse(is_plausible_person_name("去许绍华"))

        notice = NoticeMeta(
            bureau_code="liaoning_col313",
            title="国家税务总局辽阳市税务局任免工作人员（2021年4月28日）",
            source_url="http://liaoning.example/n.html",
            raw_text=(
                "决定：免去许绍华国家税务总局辽阳市税务局第一稽查局副局长职务。"
                "任命肖乐为国家税务总局辽阳市税务局第一稽查局副局长。"
            ),
        )
        events = extract_appointment_events(notice)
        names = {(e.person_name, e.action) for e in events}
        self.assertIn(("许绍华", "dismiss"), names)
        self.assertIn(("肖乐", "appoint"), names)
        self.assertNotIn("去许绍华", {e.person_name for e in events})


if __name__ == "__main__":
    unittest.main()
