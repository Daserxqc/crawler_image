from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from tax_platform.store.anomalies import apply_correction, list_anomalies, scan_anomalies
from tax_platform.store.schema import connect
import tax_platform.web.app as api_mod
from tax_platform.web.app import app


class AnomalyCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "a.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url)
            VALUES ('beijing', '任免', 'https://example.com/n')
            """
        )
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'beijing', '命陈双格', 'appoint', '决定，任命陈双格为政策法规处', '副处长',
                      NULL, '任免', 'https://example.com/e1', '任命陈双格为政策法规处副处长')
            """,
            (nid,),
        )
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'beijing', '王五', 'appoint', '政策法规处', '副处长',
                      '2024-01-01', '任免', 'https://example.com/e2', '王五任副处长')
            """,
            (nid,),
        )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current)
            VALUES ('beijing:命陈双格', '命陈双格', 'beijing', '副处长'),
                   ('beijing:王五', '王五', 'beijing', '副处长')
            """
        )
        self.conn.commit()
        self.event_bad = self.conn.execute(
            "SELECT id FROM appointment_events WHERE person_name='命陈双格'"
        ).fetchone()[0]
        self._orig = api_mod.DB_PATH
        api_mod.DB_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self) -> None:
        api_mod.DB_PATH = self._orig
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def test_scan_detects_implausible_and_noise(self) -> None:
        stats = scan_anomalies(conn=self.conn)
        self.assertGreaterEqual(stats["open_count"], 2)
        items = list_anomalies(status="open", conn=self.conn)["items"]
        kinds = {i["kind"] for i in items}
        self.assertIn("implausible_name", kinds)
        self.assertTrue({"missing_date", "parse_noise"} & kinds)

    def test_apply_correction_renames_and_resolves(self) -> None:
        scan_anomalies(conn=self.conn)
        anomaly = next(
            i
            for i in list_anomalies(kind="implausible_name", conn=self.conn)["items"]
            if i["target_id"] == str(self.event_bad)
        )
        result = apply_correction(
            target_type="appointment_event",
            target_id=str(self.event_bad),
            patch={"person_name": "陈双格", "department_raw": "政策法规处"},
            note="修正人名与科室",
            anomaly_id=anomaly["id"],
            conn=self.conn,
        )
        self.assertEqual(result["new"]["person_name"], "陈双格")
        row = self.conn.execute(
            "SELECT person_name, department_raw FROM appointment_events WHERE id=?",
            (self.event_bad,),
        ).fetchone()
        self.assertEqual(row["person_name"], "陈双格")
        self.assertEqual(row["department_raw"], "政策法规处")
        person = self.conn.execute(
            "SELECT id, name FROM persons WHERE bureau_code='beijing' AND name='陈双格'"
        ).fetchone()
        self.assertIsNotNone(person)
        status = self.conn.execute(
            "SELECT status FROM data_anomalies WHERE id=?", (anomaly["id"],)
        ).fetchone()["status"]
        self.assertEqual(status, "resolved")

    def test_rename_syncs_sibling_events(self) -> None:
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (
                (SELECT id FROM notices LIMIT 1), 'beijing', '命陈双格', 'dismiss',
                '政策法规处', '副处长', '2025-02-01', '任免',
                'https://example.com/e3', '免去命陈双格的副处长职务'
            )
            """
        )
        self.conn.commit()
        sibling_ids = [
            r["id"]
            for r in self.conn.execute(
                "SELECT id FROM appointment_events WHERE person_name='命陈双格'"
            )
        ]
        self.assertGreaterEqual(len(sibling_ids), 2)
        apply_correction(
            target_type="appointment_event",
            target_id=str(sibling_ids[0]),
            patch={"person_name": "陈双格"},
            note="同步改名",
            conn=self.conn,
        )
        left = self.conn.execute(
            "SELECT count(*) FROM appointment_events WHERE person_name='命陈双格'"
        ).fetchone()[0]
        renamed = self.conn.execute(
            "SELECT count(*) FROM appointment_events WHERE person_name='陈双格'"
        ).fetchone()[0]
        self.assertEqual(left, 0)
        self.assertGreaterEqual(renamed, 2)

    def test_api_scan_and_correct(self) -> None:
        r = self.client.post("/api/anomalies/scan")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["open_count"], 1)
        listed = self.client.get("/api/anomalies", params={"kind": "implausible_name"})
        self.assertEqual(listed.status_code, 200)
        item = next(i for i in listed.json()["items"] if i["target_id"] == str(self.event_bad))
        fixed = self.client.post(
            "/api/corrections",
            json={
                "target_type": "appointment_event",
                "target_id": str(self.event_bad),
                "patch": {"person_name": "陈双格"},
                "anomaly_id": item["id"],
                "note": "api fix",
            },
        )
        self.assertEqual(fixed.status_code, 200)
        self.assertEqual(fixed.json()["new"]["person_name"], "陈双格")


if __name__ == "__main__":
    unittest.main()
