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
from tax_platform.store.tenure import build_current_from_history


class TenureTests(unittest.TestCase):
    def test_newer_dismiss_blocks_older_appoint(self) -> None:
        history = [
            {
                "change_type": "dismiss",
                "title": "副处长",
                "department": "政策法规处",
                "date": "2025-01-01",
                "source_url": "https://example.com/d",
            },
            {
                "change_type": "appoint",
                "title": "副处长",
                "department": "政策法规处",
                "date": "2024-01-01",
                "unit": "上海市税务局",
                "source_url": "https://example.com/a",
            },
        ]
        current = build_current_from_history(history)
        self.assertFalse(current["is_current"])
        self.assertIsNone(current["since"])
        self.assertIsNone(current["source_url"])

    def test_leader_does_not_override_dismiss(self) -> None:
        history = [
            {
                "change_type": "dismiss",
                "title": "副处长",
                "department": "政策法规处",
                "date": "2025-01-01",
            },
            {
                "change_type": "appoint",
                "title": "副处长",
                "department": "政策法规处",
                "date": "2024-01-01",
            },
        ]
        leader = {
            "title_raw": "副处长",
            "departments_json": '["政策法规处"]',
            "source_url": "https://example.com/l",
        }
        current = build_current_from_history(history, leader=leader)
        self.assertFalse(current["is_current"])

    def test_open_appoint_is_current(self) -> None:
        history = [
            {
                "change_type": "appoint",
                "title": "处长",
                "department": "政策法规处",
                "date": "2024-06-01",
                "source_url": "https://example.com/a",
            },
        ]
        current = build_current_from_history(history)
        self.assertTrue(current["is_current"])
        self.assertEqual(current["title"], "处长")


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

                # Re-crawl same URL with fuller body — notice must update, not keep empty stub.
                ingest_appointment_results(
                    [
                        {
                            "bureau": "pdtax",
                            "notices": [
                                {
                                    "bureau_code": "pdtax",
                                    "title": "关于赵健健等职务任免的通知（更新）",
                                    "source_url": "https://example.test/pdtax/rsrm/202606/t480619.html",
                                    "issued_on": "2026-06-04",
                                    "raw_text": "经研究，决定：赵健健任法制科副科长。免去某某职务。" * 3,
                                }
                            ],
                            "events": [],
                        }
                    ],
                    conn=conn,
                )
                conn.commit()
                notice = conn.execute(
                    "SELECT title, raw_text FROM notices WHERE source_url = ?",
                    ("https://example.test/pdtax/rsrm/202606/t480619.html",),
                ).fetchone()
                self.assertIn("更新", notice["title"])
                self.assertGreater(len(notice["raw_text"]), 20)

                # Persisted tenure after ingest.
                person = conn.execute(
                    "SELECT is_current, title_current FROM persons WHERE id = ?",
                    (person_id("pdtax", "赵健健"),),
                ).fetchone()
                self.assertEqual(person["is_current"], 1)
                self.assertTrue(person["title_current"])
            finally:
                conn.close()

    def test_body_change_updates_event_keeps_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.db"
            conn = connect(db)
            try:
                url = "https://example.test/n1"
                first = [
                    {
                        "bureau": "pdtax",
                        "notices": [
                            {
                                "bureau_code": "pdtax",
                                "title": "任免",
                                "source_url": url,
                                "raw_text": "赵健健任法制科副科长。",
                            }
                        ],
                        "events": [
                            {
                                "person_name": "赵健健",
                                "action": "appoint",
                                "department_raw": "法制科",
                                "title_raw": "副科长",
                                "effective_on": "2026-01-01",
                                "source_url": url,
                                "notice_title": "任免",
                                "raw_clause": "赵健健任法制科副科长。",
                            }
                        ],
                    }
                ]
                ingest_appointment_results(first, conn=conn)
                conn.commit()
                eid = conn.execute(
                    "SELECT id FROM appointment_events WHERE source_url=?", (url,)
                ).fetchone()["id"]

                second = [
                    {
                        "bureau": "pdtax",
                        "notices": [
                            {
                                "bureau_code": "pdtax",
                                "title": "任免（修订）",
                                "source_url": url,
                                "raw_text": "赵健健任法制科副科长。" + "补充说明。" * 20,
                            }
                        ],
                        "events": [
                            {
                                "person_name": "赵健健",
                                "action": "appoint",
                                "department_raw": "法制科",
                                "title_raw": "副科长（试用期一年）",
                                "effective_on": "2026-01-01",
                                "source_url": url,
                                "notice_title": "任免（修订）",
                                "raw_clause": "赵健健任法制科副科长。",
                            }
                        ],
                    }
                ]
                ingest_appointment_results(second, conn=conn)
                conn.commit()
                row = conn.execute(
                    "SELECT id, title_raw FROM appointment_events WHERE source_url=?",
                    (url,),
                ).fetchone()
                self.assertEqual(row["id"], eid)
                self.assertIn("试用期", row["title_raw"] or "")
            finally:
                conn.close()

    def test_dismiss_persists_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "d.db"
            conn = connect(db)
            try:
                payload = [
                    {
                        "bureau": "shanghai",
                        "notices": [
                            {
                                "bureau_code": "shanghai",
                                "title": "任免",
                                "source_url": "https://example.com/a",
                                "raw_text": "李乙任政策法规处副处长。",
                            },
                            {
                                "bureau_code": "shanghai",
                                "title": "免职",
                                "source_url": "https://example.com/d",
                                "raw_text": "免去李乙的政策法规处副处长职务。",
                            },
                        ],
                        "events": [
                            {
                                "person_name": "李乙",
                                "action": "appoint",
                                "department_raw": "政策法规处",
                                "title_raw": "副处长",
                                "effective_on": "2024-01-01",
                                "source_url": "https://example.com/a",
                                "notice_title": "任免",
                                "raw_clause": "李乙任政策法规处副处长。",
                            },
                            {
                                "person_name": "李乙",
                                "action": "dismiss",
                                "department_raw": "政策法规处",
                                "title_raw": "副处长",
                                "effective_on": "2025-01-01",
                                "source_url": "https://example.com/d",
                                "notice_title": "免职",
                                "raw_clause": "免去李乙的政策法规处副处长职务。",
                            },
                        ],
                    }
                ]
                ingest_appointment_results(payload, conn=conn)
                conn.commit()
                row = conn.execute(
                    "SELECT is_current FROM persons WHERE id=?",
                    ("shanghai:李乙",),
                ).fetchone()
                self.assertEqual(row["is_current"], 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
