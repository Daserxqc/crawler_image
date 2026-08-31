from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.normalize.department import (
    department_from_title,
    normalize_department,
    split_department_raw,
)
from tax_platform.models.entities import OrgLevel
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.search.query import (
    departments_for_leader,
    leaders_for_department,
    lookup_department,
    search_people,
    suggest_departments,
)
from tax_platform.store.dept_catalog import rebuild_catalogs
from tax_platform.store.schema import connect


class DepartmentNormalizeExtraTests(unittest.TestCase):
    def test_split_and_from_title(self) -> None:
        parts = split_department_raw("政策法规处、货物和劳务税处")
        self.assertEqual(parts, ["政策法规处", "货物和劳务税处"])
        self.assertEqual(department_from_title("政策法规处副处长"), "政策法规处")

    def test_level_suffix_expansion(self) -> None:
        province = normalize_department("征管", org_level=OrgLevel.PROVINCE)
        district = normalize_department("征管", org_level=OrgLevel.DISTRICT)
        assert province and district
        self.assertTrue(province.canonical_name.endswith("处"))
        self.assertTrue(district.canonical_name.endswith("科"))

    def test_rejects_probation_as_department(self) -> None:
        self.assertIsNone(normalize_department("试用期一年处", org_level=OrgLevel.PROVINCE))
        self.assertIsNone(normalize_department("总经济师处", org_level=OrgLevel.PROVINCE))

    def test_person_name_filter(self) -> None:
        self.assertTrue(is_plausible_person_name("陈双格"))
        self.assertFalse(is_plausible_person_name("命陈双格"))
        self.assertFalse(is_plausible_person_name("省税务局"))
        self.assertFalse(is_plausible_person_name("人事"))
        self.assertFalse(is_plausible_person_name("朝阳区"))
        self.assertFalse(is_plausible_person_name("主要职责"))
        self.assertFalse(is_plausible_person_name("市局链接"))


class CatalogAndSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = connect(self.db_path)
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, published_at)
            VALUES ('zhejiang', '任免通知', 'https://example.com/n1', '2024-01-01')
            """
        )
        notice_id = self.conn.execute("SELECT id FROM notices").fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action,
                department_raw, title_raw, effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'zhejiang', '张三', 'appoint',
                      '政策法规处', '副处长', '2024-02-01', '任免通知',
                      'https://example.com/n1', '任命张三为政策法规处副处长')
            """,
            (notice_id,),
        )
        self.conn.execute(
            """
            INSERT INTO notices (bureau_code, title, source_url, published_at)
            VALUES ('pdtax', '区局任免', 'https://example.com/n2', '2024-03-01')
            """
        )
        notice2 = self.conn.execute(
            "SELECT id FROM notices WHERE bureau_code='pdtax'"
        ).fetchone()[0]
        self.conn.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action,
                department_raw, title_raw, effective_on, notice_title, source_url, raw_clause
            ) VALUES (?, 'pdtax', '李四', 'appoint',
                      '政策法规科', '副科长', '2024-03-02', '区局任免',
                      'https://example.com/n2', '任命李四为政策法规科副科长')
            """,
            (notice2,),
        )
        self.conn.execute(
            """
            INSERT INTO leader_duties (
                bureau_code, person_name, title_raw, duty_summary,
                departments_json, source_url
            ) VALUES (
                'zhejiang', '王五', '副局长', '分管法规',
                '["政策法规处"]', 'https://example.com/leader'
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO persons (id, name, bureau_code, title_current)
            VALUES
                ('zhejiang:张三', '张三', 'zhejiang', '副处长'),
                ('pdtax:李四', '李四', 'pdtax', '副科长'),
                ('zhejiang:王五', '王五', 'zhejiang', '副局长')
            """
        )
        self.conn.commit()
        rebuild_catalogs(conn=self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_catalog_splits_by_level(self) -> None:
        rows = self.conn.execute(
            "SELECT canonical_name, org_level FROM dept_catalog ORDER BY org_level, canonical_name"
        ).fetchall()
        pairs = {(r["canonical_name"], r["org_level"]) for r in rows}
        self.assertIn(("政策法规处", "province"), pairs)
        self.assertIn(("政策法规科", "district"), pairs)

    def test_search_by_department(self) -> None:
        hits = search_people(department="政策法规", conn=self.conn, limit=20)
        names = {h["name"] for h in hits}
        self.assertIn("张三", names)
        self.assertIn("李四", names)
        self.assertIn("王五", names)
        zhang = next(h for h in hits if h["name"] == "张三")
        self.assertGreaterEqual(zhang["appointment_count"], 1)
        wang = next(h for h in hits if h["name"] == "王五")
        self.assertIn("supervisor", wang["roles"])
        self.assertIn("appointee", zhang["roles"])

    def test_search_by_title_and_level(self) -> None:
        hits = search_people(title="副局长", org_level="province", conn=self.conn)
        self.assertEqual([h["name"] for h in hits], ["王五"])

    def test_suggest_dept(self) -> None:
        rows = suggest_departments("政策", org_level="province", conn=self.conn)
        self.assertTrue(any(r["canonical_name"] == "政策法规处" for r in rows))

    def test_leaders_for_department(self) -> None:
        leaders = leaders_for_department("政策法规处", org_level="province", conn=self.conn)
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0]["name"], "王五")
        self.assertEqual(leaders[0]["role"], "supervisor")

    def test_departments_for_leader(self) -> None:
        rows = departments_for_leader("王五", bureau_code="zhejiang", conn=self.conn)
        self.assertEqual(rows[0]["departments"], ["政策法规处"])

    def test_lookup_department(self) -> None:
        result = lookup_department("政策法规", org_level="province", conn=self.conn)
        self.assertEqual(result["supervisor_count"], 1)
        self.assertEqual(result["supervising_leaders"][0]["name"], "王五")
        staff_names = {s["name"] for s in result["staff"]}
        self.assertIn("张三", staff_names)
        self.assertNotIn("王五", staff_names)


if __name__ == "__main__":
    unittest.main()
