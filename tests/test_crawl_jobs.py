from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.job_io import resolve_site_codes
from tax_platform.models.entities import NoticeMeta


class CrawlJobHelperTests(unittest.TestCase):
    def test_resolve_site_codes(self) -> None:
        self.assertEqual(resolve_site_codes("pdtax"), ["pdtax"])
        codes = resolve_site_codes("all")
        self.assertIn("shanghai", codes)
        self.assertIn("pdtax", codes)
        self.assertIn("sta", codes)
        self.assertIn("guangdong", codes)
        self.assertEqual(len(codes), 48)

    def test_appointments_payload_keeps_failed_rows(self) -> None:
        result = AppointmentCrawlResult(
            bureau="pdtax",
            list_url="https://example.test/list/",
            list_count=1,
            notices=[
                NoticeMeta(
                    bureau_code="pdtax",
                    title="关于任免的通知",
                    source_url="https://example.test/a.html",
                    raw_text="赵健健任法制科副科长。",
                )
            ],
            failed=[{"url": "https://example.test/b.html", "error": "timeout"}],
        )
        payload = appointments_payload(result)
        self.assertEqual(payload["bureau"], "pdtax")
        self.assertEqual(payload["notices"][0]["title"], "关于任免的通知")
        self.assertEqual(payload["failed"][0]["error"], "timeout")


if __name__ == "__main__":
    unittest.main()
