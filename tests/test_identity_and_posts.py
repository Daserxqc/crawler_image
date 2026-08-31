from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.search.changes import post_archive
from tax_platform.store.identity import sync_person_identities
from tax_platform.store.ingest import get_person_profile
from tax_platform.store.posts import rebuild_org_posts
from tax_platform.store.schema import connect


class IdentityAndPostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "i.db"
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

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_identity_sync_and_profile(self) -> None:
        n = sync_person_identities(self.conn, force=True)
        self.conn.commit()
        self.assertEqual(n, 2)
        row = self.conn.execute(
            "SELECT identity_id FROM persons WHERE id = ?", ("shanghai:张甲",)
        ).fetchone()
        self.assertEqual(row["identity_id"], "shanghai:张甲")
        keys = self.conn.execute("SELECT COUNT(*) FROM person_name_keys").fetchone()[0]
        self.assertEqual(keys, 2)

        profile = get_person_profile("shanghai:张甲", conn=self.conn)
        assert profile is not None
        self.assertEqual(profile["identity_id"], "shanghai:张甲")
        self.assertEqual(len(profile["appearances"]), 1)

    def test_rebuild_posts_serves_archive(self) -> None:
        sync_person_identities(self.conn, force=True)
        stats = rebuild_org_posts(self.conn)
        self.conn.commit()
        self.assertGreaterEqual(stats["posts"], 2)
        self.assertGreaterEqual(stats["current"], 1)

        archive = post_archive(
            bureau_code="shanghai",
            department="政策法规处",
            conn=self.conn,
        )
        self.assertEqual(archive.get("source"), "org_posts")
        incumbents = {i["person_name"] for i in archive["incumbents"]}
        self.assertIn("张甲", incumbents)
        self.assertNotIn("李乙", incumbents)
        self.assertTrue(any(p["person_name"] == "李乙" for p in archive["past"]))

    def test_successor_closes_previous_incumbent(self) -> None:
        """后人任命同一岗位时，前人应进历任，不能两人同为现任。"""
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES
            (?, 'yuncheng', '王红岩', 'appoint', '第一稽查局', '局长', '2023-07-20',
             '任免', 'https://example.com/a', '王红岩为第一稽查局局长'),
            (?, 'yuncheng', '连向军', 'appoint', '第一稽查局', '局长', '2025-08-07',
             '任免', 'https://example.com/b', '连向军为第一稽查局局长')
            """,
            (nid, nid),
        )
        self.conn.commit()
        rebuild_org_posts(self.conn)
        self.conn.commit()

        archive = post_archive(
            bureau_code="yuncheng",
            department="第一稽查局",
            title="局长",
            conn=self.conn,
        )
        incumbents = {i["person_name"] for i in archive["incumbents"]}
        past = {i["person_name"] for i in archive["past"]}
        self.assertEqual(incumbents, {"连向军"})
        self.assertIn("王红岩", past)
        self.assertNotIn("王红岩", incumbents)


if __name__ == "__main__":
    unittest.main()
