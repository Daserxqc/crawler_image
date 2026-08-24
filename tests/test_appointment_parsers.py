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


if __name__ == "__main__":
    unittest.main()
