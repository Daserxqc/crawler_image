from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from tax_platform.search.export import rows_from_search_hits, to_csv_bytes
from tax_platform.search.query import search_people
from tax_platform.store.schema import connect
import tax_platform.web.app as api_mod
from tax_platform.web.app import app


class SearchFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "api.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on)
            VALUES ('shanghai', '任免', 'https://example.com/a', '2024-06-01')
            """
        )
        nid = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '测试甲', 'appoint', '政策法规处', '副处长',
                      '2024-06-01', '任免', 'https://example.com/a', '测试甲任政策法规处副处长')
            """,
            (nid,),
        )
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '测试甲', 'appoint', '政策法规处', '处长',
                      '2025-01-01', '任免', 'https://example.com/a2', '测试甲任政策法规处处长')
            """,
            (nid,),
        )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current)
            VALUES ('shanghai:测试甲', '测试甲', 'shanghai', '处长')
            """
        )
        self.conn.execute(
            """
            INSERT INTO leader_duties (
                bureau_code, person_name, title_raw, duty_summary, departments_json, source_url
            ) VALUES (
                'shanghai', '测试乙', '副局长', '分管', '["政策法规处"]', 'https://example.com/l'
            )
            """
        )
        self.conn.commit()
        self._orig_db = api_mod.DB_PATH
        api_mod.DB_PATH = self.db_path
        self.client = TestClient(app)

    def tearDown(self) -> None:
        api_mod.DB_PATH = self._orig_db
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def test_search_by_name_and_date(self) -> None:
        hits = search_people(
            name="测试甲",
            date_from="2025-01-01",
            date_to="2025-12-31",
            conn=self.conn,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(len(hits[0]["appointments"]), 1)
        self.assertEqual(hits[0]["appointments"][0]["title_raw"], "处长")

    def test_api_search_and_penetrate(self) -> None:
        r = self.client.get(
            "/api/search",
            params={"department": "政策法规处", "bureau_code": "shanghai"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertGreaterEqual(body["total"], 1)
        names = {i["name"] for i in body["items"]}
        self.assertIn("测试乙", names)

        # total must reflect full match set, not the page size.
        r2 = self.client.get(
            "/api/search",
            params={"department": "政策法规处", "bureau_code": "shanghai", "limit": 1},
        )
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertGreaterEqual(body2["total"], 2)
        self.assertEqual(len(body2["items"]), 1)

        p = self.client.get(
            "/api/departments/penetrate",
            params={"department": "政策法规处", "bureau_code": "shanghai"},
        )
        self.assertEqual(p.status_code, 200)
        pdata = p.json()
        self.assertGreaterEqual(pdata["supervisor_count"], 1)
        self.assertTrue(pdata["upward"])

        person = self.client.get("/api/people/shanghai:测试甲")
        self.assertEqual(person.status_code, 200)
        self.assertEqual(person.json()["name"], "测试甲")
        self.assertTrue(person.json()["history"][0]["source_url"])

    def test_api_export_csv(self) -> None:
        r = self.client.get(
            "/api/export/search",
            params={"name": "测试甲", "fmt": "csv"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["content-type"])
        self.assertIn("测试甲".encode("utf-8"), r.content)

    def test_csv_helper(self) -> None:
        hits = search_people(name="测试甲", conn=self.conn)
        raw = to_csv_bytes(rows_from_search_hits(hits))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf") or "姓名".encode("utf-8") in raw)


if __name__ == "__main__":
    unittest.main()
