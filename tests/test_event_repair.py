"""Tests for appointment event repair helpers."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.event_repair import (
    dedupe_appointment_events,
    infer_bureau_name_from_clause,
    repair_appointment_events,
)
from tax_platform.store.schema import connect


class EventRepairTests(unittest.TestCase):
    def test_infer_bureau_from_clause(self) -> None:
        clause = "刘刚为国家税务总局巴彦淖尔经济技术开发区税务局党委书记、局长、四级高级主办"
        got = infer_bureau_name_from_clause(clause, person_name="刘刚")
        self.assertIn("经济技术开发区税务局", got or "")

    def test_dedupe_semantic_twins(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        conn = connect(Path(tmp.name) / "t.db")
        conn.execute(
            "INSERT INTO notices (bureau_code, title, source_url) VALUES ('b','n','http://x/a')"
        )
        nid = conn.execute("SELECT id FROM notices").fetchone()[0]
        conn.executemany(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'b', '张三', 'appoint', '法制科', '科长', '2024-01-01',
                      '任免', ?, ?)
            """,
            [
                (nid, "http://x/a", "张三任法制科科长"),
                (nid, "https://x/a", "张三任法制科科长"),
                (nid, "http://y/b", "张三任法制科科长"),
            ],
        )
        conn.commit()
        n = dedupe_appointment_events(conn)
        self.assertEqual(n, 2)
        left = conn.execute("SELECT COUNT(*) FROM appointment_events").fetchone()[0]
        self.assertEqual(left, 1)
        conn.close()
        tmp.cleanup()

    def test_backfill_then_dedupe(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        conn = connect(Path(tmp.name) / "t.db")
        conn.execute(
            "INSERT INTO notices (bureau_code, title, source_url) VALUES ('b','n','http://x/a')"
        )
        nid = conn.execute("SELECT id FROM notices").fetchone()[0]
        conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause, bureau_name
            ) VALUES (?, 'city', '王磊', 'appoint', '党委书记', '局长', '2021-12-02',
                      '任免', 'http://a', ?, NULL)
            """,
            (
                nid,
                "王磊为国家税务总局某市乌拉特后旗税务局党委书记、局长",
            ),
        )
        conn.commit()
        stats = repair_appointment_events(conn)
        self.assertGreaterEqual(stats["bureau_filled"], 1)
        row = conn.execute(
            "SELECT bureau_name FROM appointment_events WHERE person_name='王磊'"
        ).fetchone()
        self.assertIn("乌拉特后旗", row[0])
        conn.close()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
