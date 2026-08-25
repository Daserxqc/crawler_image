from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store import get_person_profile, ingest_appointment_results, ingest_leader_results, person_id
from tax_platform.store.schema import connect


class StoreTests(unittest.TestCase):
    def test_ingest_and_profile_with_notice_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            appointments = [
                {
                    "bureau": "pdtax",
                    "notices": [
                        {
                            "bureau_code": "pdtax",
                            "title": "关于赵健健等职务任免的通知",
                            "source_url": "https://example.test/pdtax/rsrm/202606/t480619.html",
                            "issued_on": "2026-06-04",
                            "raw_text": "赵健健任法制科副科长。",
                        }
                    ],
                    "events": [
                        {
                            "person_name": "赵健健",
                            "action": "appoint",
                            "department_raw": "法制科",
                            "title_raw": "副科长",
                            "effective_on": "2026-06-04",
                            "source_url": "https://example.test/pdtax/rsrm/202606/t480619.html",
                            "notice_title": "关于赵健健等职务任免的通知",
                            "raw_clause": "赵健健任法制科副科长。",
                        }
                    ],
                }
            ]
            leaders = [
                {
                    "bureau": "pdtax",
                    "leaders": [
                        {
                            "person_name": "郑燕",
                            "gender": "女",
                            "ethnicity": "汉族",
                            "title_raw": "党委书记、局长",
                            "duty_summary": "主持全面工作",
                            "departments_raw": [],
                            "source_url": "https://example.test/pdtax/ldjj.html",
                        }
                    ],
                }
            ]
            conn = connect(db)
            try:
                ingest_appointment_results(appointments, conn=conn)
                ingest_leader_results(leaders, conn=conn)
                conn.commit()

                zhao = get_person_profile(person_id("pdtax", "赵健健"), conn=conn)
                self.assertIsNotNone(zhao)
                assert zhao is not None
                self.assertEqual(len(zhao["history"]), 1)
                self.assertEqual(zhao["history"][0]["source_url"], "https://example.test/pdtax/rsrm/202606/t480619.html")
                self.assertIn("任免", zhao["history"][0]["notice_title"])

                zheng = get_person_profile(person_id("pdtax", "郑燕"), conn=conn)
                self.assertIsNotNone(zheng)
                assert zheng is not None
                self.assertEqual(zheng["leader_intro_url"], "https://example.test/pdtax/ldjj.html")
                self.assertIn("现任", zheng["tags"])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
