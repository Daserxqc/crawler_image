from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.schema import connect
from tax_platform.store.tenure import enrich_history_rows, recompute_persons
from tax_platform.store.anomalies import apply_correction


class TenureEnrichTests(unittest.TestCase):
    def test_ended_on_from_later_dismiss(self) -> None:
        events = [
            {
                "effective_on": "2025-06-01",
                "action": "dismiss",
                "title_raw": "副处长",
                "department_raw": "政策法规处",
                "bureau_name": "上海市税务局",
                "bureau_code": "shanghai",
                "notice_title": "任免",
                "source_url": "https://example.com/d",
                "raw_clause": "免去测试甲的副处长职务",
            },
            {
                "effective_on": "2024-01-01",
                "action": "appoint",
                "title_raw": "副处长",
                "department_raw": "政策法规处",
                "bureau_name": "上海市税务局",
                "bureau_code": "shanghai",
                "notice_title": "任免",
                "source_url": "https://example.com/a",
                "raw_clause": "任命测试甲为副处长",
            },
        ]
        hist = enrich_history_rows(events, bureau_code="shanghai")
        appoint = next(r for r in hist if r["change_type"] == "appoint")
        self.assertEqual(appoint["started_on"], "2024-01-01")
        self.assertEqual(appoint["ended_on"], "2025-06-01")

    def test_correction_recomputes_person(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "t.db"
        conn = connect(db_path)
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO notices (bureau_code, title, source_url) VALUES ('shanghai', '任免', 'https://example.com/n')"
        )
        nid = conn.execute("SELECT id FROM notices").fetchone()[0]
        conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '测试甲', 'appoint', '政策法规处', '副处长',
                      '2024-01-01', '任免', 'https://example.com/a', '任命测试甲为副处长')
            """,
            (nid,),
        )
        eid = conn.execute("SELECT id FROM appointment_events").fetchone()[0]
        recompute_persons(conn, {("shanghai", "测试甲")})
        conn.commit()
        before = conn.execute(
            "SELECT title_current, department_current FROM persons WHERE id='shanghai:测试甲'"
        ).fetchone()
        self.assertIsNotNone(before)

        result = apply_correction(
            target_type="appointment_event",
            target_id=str(eid),
            patch={"title_raw": "处长"},
            note="改职务",
            conn=conn,
        )
        self.assertGreaterEqual(result.get("recomputed_persons", 0), 1)
        after = conn.execute(
            "SELECT title_current FROM persons WHERE id='shanghai:测试甲'"
        ).fetchone()
        self.assertIsNotNone(after)
        self.assertIn("处长", after["title_current"] or "")


if __name__ == "__main__":
    unittest.main()
