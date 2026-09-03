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

    def test_batch_district_chiefs_not_one_post(self) -> None:
        """市局一篇任免任命多个区县局长时，不能合成同一岗位。"""
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, bureau_name,
                department_raw, title_raw, effective_on, notice_title, source_url, raw_clause
            ) VALUES
            (?, 'city', '刘勇', 'appoint', '国家税务总局某市临河区税务局',
             '党委书记', '局长、四级高级主办', '2021-12-02', '任免', 'https://example.com/a',
             '刘勇为临河区税务局党委书记、局长'),
            (?, 'city', '王磊', 'appoint', '国家税务总局某市乌拉特后旗税务局',
             '党委书记', '局长、四级高级主办', '2021-12-02', '任免', 'https://example.com/a',
             '王磊为乌拉特后旗税务局党委书记、局长'),
            (?, 'city', '刘刚', 'appoint', '国家税务总局某市经济技术开发区税务局',
             '党委书记', '局长、四级高级主办', '2021-12-02', '任免', 'https://example.com/a',
             '刘刚为经济技术开发区税务局党委书记、局长')
            """,
            (nid, nid, nid),
        )
        self.conn.commit()
        rebuild_org_posts(self.conn)
        self.conn.commit()

        for unit, person in (
            ("临河区 · 党委书记", "刘勇"),
            ("乌拉特后旗 · 党委书记", "王磊"),
            ("经济技术开发区 · 党委书记", "刘刚"),
        ):
            archive = post_archive(
                bureau_code="city",
                department=unit,
                title="局长、四级高级主办",
                conn=self.conn,
            )
            incumbents = {i["person_name"] for i in archive["incumbents"]}
            past = {i["person_name"] for i in archive["past"]}
            self.assertEqual(incumbents, {person}, unit)
            self.assertEqual(past, set(), unit)

    def test_deputy_same_day_multiple_incumbents(self) -> None:
        """同日任命多名副所长，应并存现任，不应产生假历任。"""
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        names = ["王甲", "王乙", "王丙"]
        for nm in names:
            self.conn.execute(
                """
                INSERT INTO appointment_events (
                    notice_id, bureau_code, person_name, action,
                    department_raw, title_raw, effective_on, notice_title,
                    source_url, raw_clause
                ) VALUES (?, 'qx', ?, 'appoint', '南城税务所', '副所长',
                          '2021-07-30', '任免', 'https://example.com/a', ?)
                """,
                (nid, nm, f"{nm}任副所长"),
            )
        self.conn.commit()
        rebuild_org_posts(self.conn)
        archive = post_archive(
            bureau_code="qx",
            department="南城税务所",
            title="副所长",
            conn=self.conn,
        )
        incumbents = {i["person_name"] for i in archive["incumbents"]}
        self.assertEqual(incumbents, set(names))
        self.assertEqual(len(archive["past"]), 0)


if __name__ == "__main__":
    unittest.main()
