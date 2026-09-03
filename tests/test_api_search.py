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

        # total / current_count must reflect full match set, not the page size.
        r2 = self.client.get(
            "/api/search",
            params={"department": "政策法规处", "bureau_code": "shanghai", "limit": 1},
        )
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        self.assertGreaterEqual(body2["total"], 2)
        self.assertEqual(len(body2["items"]), 1)
        self.assertIn("current_count", body2)
        self.assertEqual(body2["current_count"], body["current_count"])

        r3 = self.client.get(
            "/api/search",
            params={"department": "政策法规处", "bureau_code": "shanghai", "limit": 1, "offset": 1},
        )
        self.assertEqual(r3.status_code, 200)
        body3 = r3.json()
        self.assertEqual(body3["offset"], 1)
        self.assertEqual(len(body3["items"]), 1)
        self.assertNotEqual(body2["items"][0]["name"], body3["items"][0]["name"])
        self.assertEqual(body3["current_count"], body["current_count"])

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

    def test_appointment_fallback_by_name_across_bureau(self) -> None:
        """Leader under bureau A with events only under bureau B still gets records."""
        from tax_platform.search.query import _person_appointments

        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (1, 'sta', '跨局甲', 'appoint', '政策法规司', '副司长',
                      '2024-01-01', '任免', 'https://example.com/sta', '跨局甲任政策法规司副司长')
            """
        )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current)
            VALUES ('shanghai:跨局甲', '跨局甲', 'shanghai', NULL)
            """
        )
        self.conn.commit()
        rows = _person_appointments(self.conn, "shanghai", "跨局甲")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bureau_code"], "sta")
        hits = search_people(name="跨局甲", bureau_code="shanghai", conn=self.conn)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["appointment_count"], 1)

    def test_region_browse_hq_category_conflicts_with_local_bureau(self) -> None:
        """直属 + 地方局 must not report local headcount with an empty page."""
        from tax_platform.search.query import search_people_page

        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current, is_current)
            VALUES
              ('neimenggu_alashan:甲', '甲', 'neimenggu_alashan', '局长', 1),
              ('neimenggu_alashan:乙', '乙', 'neimenggu_alashan', '副局长', 1),
              ('sta:丙', '丙', 'sta', '主任', 1)
            """
        )
        self.conn.commit()

        total, current, items = search_people_page(
            unit_category="direct",
            bureau_code="neimenggu_alashan",
            limit=50,
            offset=0,
            conn=self.conn,
        )
        self.assertEqual(total, 0)
        self.assertEqual(current, 0)
        self.assertEqual(items, [])

        # HQ category alone still resolves against STA (may be empty if no direct units).
        total2, _, items2 = search_people_page(
            unit_category="direct",
            org_level="headquarters",
            limit=50,
            offset=0,
            conn=self.conn,
        )
        self.assertEqual(total2, len(items2))
        for hit in items2:
            self.assertEqual(hit.get("unit_category"), "direct")

    def test_hq_dispatched_total_is_filtered_not_all_sta(self) -> None:
        """派出 total must be the dispatched roster, not COUNT(*) of all STA persons."""
        from tax_platform.search.query import search_people_page

        surnames = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨"
        internals = [
            f"{surnames[i % 16]}{surnames[(i // 16) % 16]}平" for i in range(60)
        ]
        dispatched = ["韩荣", "马青", "刘波", "陈军"]
        for name in internals:
            self.conn.execute(
                """
                INSERT INTO persons (id, name, bureau_code, title_current, is_current)
                VALUES (?, ?, 'sta', '司长', 1)
                """,
                (f"sta:{name}", name),
            )
        for name in dispatched:
            self.conn.execute(
                """
                INSERT INTO persons (id, name, bureau_code, title_current, is_current)
                VALUES (?, ?, 'sta', '司长', 1)
                """,
                (f"sta:{name}", name),
            )
            self.conn.execute(
                """
                INSERT INTO leader_duties (
                    bureau_code, person_name, title_raw, duty_summary, departments_json, source_url
                ) VALUES (
                    'sta', ?, '特派员', '', '["驻北京特派员办事处"]', 'https://example.com/d'
                )
                """,
                (name,),
            )
        self.conn.commit()

        total, current, page1 = search_people_page(
            org_level="headquarters",
            unit_category="dispatched",
            limit=2,
            offset=0,
            conn=self.conn,
        )
        self.assertEqual(total, 4)
        self.assertEqual(current, 4)
        self.assertEqual(len(page1), 2)
        for hit in page1:
            self.assertEqual(hit.get("unit_category"), "dispatched")

        total2, _, page2 = search_people_page(
            org_level="headquarters",
            unit_category="dispatched",
            limit=2,
            offset=2,
            conn=self.conn,
        )
        self.assertEqual(total2, 4)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual({h["name"] for h in page1}, {h["name"] for h in page2})

        # Keyword path (name filter) must also count after category classification.
        total3, _, items3 = search_people_page(
            name="韩",
            org_level="headquarters",
            unit_category="dispatched",
            limit=50,
            offset=0,
            conn=self.conn,
        )
        self.assertEqual(total3, 1)
        self.assertEqual([h["name"] for h in items3], ["韩荣"])


if __name__ == "__main__":
    unittest.main()
