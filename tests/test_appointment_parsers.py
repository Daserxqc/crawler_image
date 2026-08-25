from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_clauses import extract_appointment_events, split_post
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list

FIXTURES = ROOT / "tests" / "fixtures"


class AppointmentListTests(unittest.TestCase):
    def test_keeps_appointment_links_and_skips_recruiting(self) -> None:
        html = (FIXTURES / "appointment_list.html").read_text(encoding="utf-8")
        items = parse_appointment_list(html, "https://shanghai.chinatax.gov.cn/pdtax/xxgk/rsrm/")
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].source_url.endswith("/pdtax/xxgk/rsrm/202606/t480619.html"))
        self.assertEqual(str(items[0].published_on), "2026-06-04")
        self.assertNotIn("体检", "".join(item.title for item in items))


class AppointmentDetailTests(unittest.TestCase):
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


class AppointmentClauseTests(unittest.TestCase):
    def test_split_post(self) -> None:
        bureau, department, title = split_post("保税区税务分局法制科副科长")
        self.assertEqual(bureau, "保税区税务分局")
        self.assertEqual(department, "法制科")
        self.assertEqual(title, "副科长")

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


if __name__ == "__main__":
    unittest.main()
