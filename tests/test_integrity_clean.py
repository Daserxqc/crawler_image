# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.integrity_clean import (
    BUREAU_LEVEL_DEPT,
    purge_orphan_bureau_codes,
    remap_sta_local_events,
    resolve_event_posting_bureau,
)
from tax_platform.store.posts import post_department_key, rebuild_org_posts
from tax_platform.store.schema import connect


class IntegrityCleanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_resolve_sta_to_province(self) -> None:
        code = resolve_event_posting_bureau(
            "sta",
            "国家税务总局江西省税务局",
        )
        self.assertEqual(code, "jiangxi")

    def test_resolve_keeps_true_hq(self) -> None:
        code = resolve_event_posting_bureau("sta", "国家税务总局")
        self.assertEqual(code, "sta")

    def test_bureau_level_dept_key(self) -> None:
        key = post_department_key(
            "jiangxi",
            None,
            "国家税务总局江西省税务局",
        )
        self.assertEqual(key, BUREAU_LEVEL_DEPT)

    def test_purge_orphan_leaders(self) -> None:
        self.conn.execute(
            """
            INSERT INTO leader_duties (bureau_code, person_name, title_raw, source_url)
            VALUES ('hunan_path_login', '张三', '局长', 'https://example.com/a'),
                   ('hunan', '张三', '局长', 'https://example.com/b')
            """
        )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code)
            VALUES ('hunan_path_login:张三', '张三', 'hunan_path_login'),
                   ('hunan:张三', '张三', 'hunan')
            """
        )
        self.conn.commit()
        purge_orphan_bureau_codes(self.conn, dry_run=False)
        n_junk = self.conn.execute(
            "SELECT COUNT(*) FROM leader_duties WHERE bureau_code='hunan_path_login'"
        ).fetchone()[0]
        n_ok = self.conn.execute(
            "SELECT COUNT(*) FROM leader_duties WHERE bureau_code='hunan'"
        ).fetchone()[0]
        self.assertEqual(n_junk, 0)
        self.assertEqual(n_ok, 1)

    def test_remap_and_rebuild_sta_province_post(self) -> None:
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url)
            VALUES ('sta', '任免', 'https://example.com/n')
            """
        )
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, bureau_name,
                department_raw, title_raw, effective_on, notice_title, source_url, raw_clause
            ) VALUES (
                ?, 'sta', '李四', 'appoint', '国家税务总局江西省税务局',
                NULL, '副局长', '2024-01-01', '任免', 'https://example.com/n',
                '任命李四为国家税务总局江西省税务局副局长'
            )
            """,
            (nid,),
        )
        self.conn.commit()

        stats = remap_sta_local_events(self.conn, dry_run=False)
        self.assertEqual(stats["remapped"], 1)
        row = self.conn.execute(
            "SELECT bureau_code FROM appointment_events WHERE person_name='李四'"
        ).fetchone()
        self.assertEqual(row["bureau_code"], "jiangxi")

        rebuild_org_posts(self.conn)
        post = self.conn.execute(
            """
            SELECT bureau_code, department, title FROM org_posts
            WHERE title='副局长'
            """
        ).fetchone()
        self.assertIsNotNone(post)
        self.assertEqual(post["bureau_code"], "jiangxi")
        self.assertEqual(post["department"], BUREAU_LEVEL_DEPT)


if __name__ == "__main__":
    unittest.main()
