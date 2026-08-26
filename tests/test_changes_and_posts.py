from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from tax_platform.normalize.change import classify_change
from tax_platform.models.entities import ChangeType
from tax_platform.search.changes import list_changes, post_archive
from tax_platform.store.ingest import get_person_profile
from tax_platform.store.schema import connect
import tax_platform.web.app as api_mod
from tax_platform.web.app import app


class ChangeTypeTests(unittest.TestCase):
    def test_classify_basics(self) -> None:
        self.assertEqual(
            classify_change(action="dismiss", raw_clause="免去张三的处长职务"),
            ChangeType.DISMISS,
        )
        self.assertEqual(
            classify_change(action="appoint", notice_title="试用期满转正任职的通知"),
            ChangeType.PROBATION_CONFIRM,
        )
        self.assertEqual(
            classify_change(
                action="appoint",
                title_raw="处长",
                department_raw="政策法规处",
                previous_title="副处长",
                previous_department="政策法规处",
            ),
            ChangeType.PROMOTE,
        )


class ChangesAndPostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "c.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url)
            VALUES ('shanghai', '任免', 'https://example.com/n')
            """
        )
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        rows = [
            ("张甲", "appoint", "政策法规处", "副处长", "2023-01-01", "https://example.com/1"),
            ("张甲", "appoint", "政策法规处", "处长", "2024-06-01", "https://example.com/2"),
            ("李乙", "appoint", "政策法规处", "副处长", "2024-07-01", "https://example.com/3"),
            ("李乙", "dismiss", "政策法规处", "副处长", "2025-01-01", "https://example.com/4"),
        ]
        for name, action, dept, title, day, url in rows:
            self.conn.execute(
                """
                INSERT INTO appointment_events (
                    notice_id, bureau_code, person_name, action, department_raw, title_raw,
                    effective_on, notice_title, source_url, raw_clause
                ) VALUES (?, 'shanghai', ?, ?, ?, ?, ?, '任免', ?, ?)
                """,
                (nid, name, action, dept, title, day, url, f"{name}{action}{title}"),
            )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current)
            VALUES ('shanghai:张甲', '张甲', 'shanghai', '处长'),
                   ('shanghai:李乙', '李乙', 'shanghai', '副处长')
            """
        )
        self.conn.commit()
        self._orig = api_mod.DB_PATH
        api_mod.DB_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self) -> None:
        api_mod.DB_PATH = self._orig
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def test_feed_and_post(self) -> None:
        feed = list_changes(bureau_code="shanghai", department="政策法规", limit=10, conn=self.conn)
        self.assertGreaterEqual(feed["total"], 3)
        types = {i["change_type"] for i in feed["items"]}
        self.assertIn("dismiss", types)

        archive = post_archive(
            bureau_code="shanghai",
            department="政策法规处",
            conn=self.conn,
        )
        incumbents = {i["person_name"] for i in archive["incumbents"]}
        self.assertIn("张甲", incumbents)
        self.assertNotIn("李乙", incumbents)
        self.assertTrue(any(p["person_name"] == "李乙" for p in archive["past"]))

    def test_profile_current_uses_latest_appoint(self) -> None:
        profile = get_person_profile("shanghai:张甲", conn=self.conn)
        assert profile is not None
        self.assertEqual(profile["current"]["title"], "处长")
        self.assertTrue(profile["current"]["is_current"])
        self.assertEqual(profile["history"][0]["change_type"], "promote")

        li = get_person_profile("shanghai:李乙", conn=self.conn)
        assert li is not None
        self.assertFalse(li["current"]["is_current"])

    def test_api_changes_and_posts(self) -> None:
        r = self.client.get("/api/changes", params={"bureau_code": "shanghai", "limit": 10})
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["total"], 3)

        p = self.client.get(
            "/api/posts",
            params={"bureau_code": "shanghai", "department": "政策法规处", "title": "处长"},
        )
        self.assertEqual(p.status_code, 200)
        names = {i["person_name"] for i in p.json()["incumbents"]}
        self.assertIn("张甲", names)


if __name__ == "__main__":
    unittest.main()
