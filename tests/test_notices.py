from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from tax_platform.search.notices import list_notices
from tax_platform.store.schema import connect
import tax_platform.web.app as api_mod
from tax_platform.web.app import app


class NoticesFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "n.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on, published_at, doc_no)
            VALUES
              ('shanghai', '上海任免甲', 'https://example.com/a', '2025-08-01', '2025-08-02', '沪税任〔2025〕1号'),
              ('shanghai', '上海任免乙', 'https://example.com/b', '2024-01-01', '2024-01-02', NULL),
              ('beijing', '北京任免', 'https://example.com/c', '2025-09-01', NULL, NULL)
            """
        )
        nid = self.conn.execute(
            "SELECT id FROM notices WHERE source_url = 'https://example.com/a'"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'shanghai', '张三', 'appoint', '政策法规处', '处长',
                      '2025-08-01', '上海任免甲', 'https://example.com/a', '张三为处长')
            """,
            (nid,),
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

    def test_list_notices_newest_first(self) -> None:
        data = list_notices(limit=10, conn=self.conn)
        self.assertEqual(data["total"], 3)
        dates = [i["sort_date"] for i in data["items"]]
        self.assertEqual(dates, ["2025-09-01", "2025-08-01", "2024-01-01"])
        shanghai = next(i for i in data["items"] if i["source_url"].endswith("/a"))
        self.assertEqual(shanghai["event_count"], 1)

    def test_list_notices_date_from(self) -> None:
        data = list_notices(date_from="2025-01-01", limit=10, conn=self.conn)
        self.assertEqual(data["total"], 2)
        self.assertTrue(all((i["sort_date"] or "") >= "2025-01-01" for i in data["items"]))

    def test_list_notices_unit_category_municipality(self) -> None:
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on)
            VALUES
              ('hebei', '河北任免', 'https://example.com/hb', '2025-08-15'),
              ('tianjin', '天津任免', 'https://example.com/tj', '2025-08-16')
            """
        )
        self.conn.commit()
        data = list_notices(
            org_level="province",
            unit_category="municipality",
            date_from="2025-01-01",
            limit=50,
            conn=self.conn,
        )
        codes = {i["bureau_code"] for i in data["items"]}
        self.assertTrue({"shanghai", "beijing", "tianjin"} & codes)
        self.assertNotIn("hebei", codes)

    def test_list_notices_hq_category_uses_event_text(self) -> None:
        """直属/派出 must not share the same COUNT(*) of all STA notices."""
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, issued_on)
            VALUES
              ('sta', '内设任免', 'https://example.com/sta-i', '2025-09-01'),
              ('sta', '直属任免', 'https://example.com/sta-d', '2025-09-02'),
              ('sta', '派出任免', 'https://example.com/sta-p', '2025-09-03')
            """
        )
        rows = self.conn.execute(
            "SELECT id, title FROM notices WHERE bureau_code='sta' ORDER BY id"
        ).fetchall()
        by_title = {r["title"]: r["id"] for r in rows}
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, department_raw, title_raw,
                bureau_name, effective_on, notice_title, source_url, raw_clause
            ) VALUES
              (?, 'sta', '甲', 'appoint', '办公厅', '司长', '国家税务总局',
               '2025-09-01', '内设任免', 'https://example.com/sta-i', '甲任办公厅司长'),
              (?, 'sta', '乙', 'appoint', '中国税务出版社', '社长', '中国税务出版社',
               '2025-09-02', '直属任免', 'https://example.com/sta-d', '乙任出版社社长'),
              (?, 'sta', '丙', 'appoint', '驻广州特派员办事处', '特派员', '驻广州特派办',
               '2025-09-03', '派出任免', 'https://example.com/sta-p', '丙任特派员')
            """,
            (
                by_title["内设任免"],
                by_title["直属任免"],
                by_title["派出任免"],
            ),
        )
        self.conn.commit()

        direct = list_notices(unit_category="direct", limit=50, conn=self.conn)
        self.assertEqual(direct["total"], 1)
        self.assertEqual(direct["items"][0]["title"], "直属任免")

        dispatched = list_notices(unit_category="dispatched", limit=50, conn=self.conn)
        self.assertEqual(dispatched["total"], 1)
        self.assertEqual(dispatched["items"][0]["title"], "派出任免")

        internal = list_notices(unit_category="internal", limit=50, conn=self.conn)
        self.assertEqual(internal["total"], 1)
        self.assertEqual(internal["items"][0]["title"], "内设任免")

    def test_api_notices(self) -> None:
        r = self.client.get("/api/notices", params={"bureau_code": "shanghai", "limit": 10})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertTrue(body["items"][0]["sort_date"] >= body["items"][1]["sort_date"])

        page = self.client.get("/notices")
        self.assertEqual(page.status_code, 200)


if __name__ == "__main__":
    unittest.main()
