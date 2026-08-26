from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import ALL_SITES, get_site, list_sites
from tax_platform.config.sites_headquarters import STA_HEADQUARTERS
from tax_platform.normalize.department import normalize_department
from tax_platform.normalize.title import normalize_title, org_level_sort_rank, title_sort_rank
from tax_platform.models.entities import OrgLevel
from tax_platform.search.display import enrich_hit_display, resolve_posting_site


class SiteRegistryTests(unittest.TestCase):
    def test_national_registry_size(self) -> None:
        # Built-in catalog is 48; optional city registry may add more.
        self.assertGreaterEqual(len(ALL_SITES), 48)
        self.assertEqual(get_site("sta").level, "headquarters")
        self.assertEqual(get_site("zhejiang").level, "province")
        self.assertEqual(get_site("pdtax").level, "district")
        self.assertEqual(len(list_sites("province")), 31)

    def test_headquarters_parent(self) -> None:
        self.assertIsNone(STA_HEADQUARTERS.parent_code)
        self.assertEqual(get_site("guangdong").parent_code, "sta")


class NormalizeTests(unittest.TestCase):
    def test_normalize_title_strips_hosting(self) -> None:
        result = normalize_title("副局长（副厅局级）主持分管工作")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rank_hint, "副厅局级")
        self.assertIn("副局长", result.canonical)

    def test_title_and_org_sort_rank(self) -> None:
        self.assertLess(title_sort_rank("局长"), title_sort_rank("副局长"))
        self.assertLess(title_sort_rank("副局长"), title_sort_rank("处长"))
        self.assertLess(title_sort_rank("处长"), title_sort_rank("科长"))
        self.assertLess(
            title_sort_rank("副局长、二级高级主办"),
            title_sort_rank("处长"),
        )
        self.assertLess(org_level_sort_rank("headquarters"), org_level_sort_rank("province"))
        self.assertLess(org_level_sort_rank("province"), org_level_sort_rank("district"))

    def test_normalize_department_kind(self) -> None:
        dept = normalize_department("个人所得税处", org_level=OrgLevel.PROVINCE)
        self.assertIsNotNone(dept)
        assert dept is not None
        self.assertEqual(dept.kind.value, "chu")


class PostingSiteTests(unittest.TestCase):
    def test_sta_prefix_local_bureau_is_not_headquarters(self) -> None:
        cases = [
            ("国家税务总局贵州省税务局", "guizhou", "province"),
            ("国家税务总局中新天津生态城税务局", "tianjin", "district"),
            ("国家税务总局临夏回族自治州税务局", "gansu", "city"),
            ("国家税务总局五家渠税务局", "xinjiang", "city"),
            ("国家税务总局厦门市税务局", "sta", "city"),
            ("国家税务总局伊犁哈萨克自治州税务局", "sta", "city"),
            ("国家税务总局哈尔滨市税务局", "sta", "city"),
        ]
        for unit, fallback, expect_level in cases:
            from tax_platform.search.display import _posting_org_level

            level, _code = _posting_org_level(fallback, {"unit": unit})
            self.assertEqual(level, expect_level, msg=unit)
            site = resolve_posting_site(fallback, {"unit": unit})
            if site is not None:
                self.assertNotEqual(site.code, "sta", msg=unit)

    def test_true_sta_headquarters_units(self) -> None:
        for unit in (
            "国家税务总局",
            "国家税务总局货物和劳务税司",
            "国家税务总局办公厅",
        ):
            site = resolve_posting_site("sta", {"unit": unit})
            self.assertIsNotNone(site)
            assert site is not None
            self.assertEqual(site.code, "sta")
            self.assertEqual(site.level, "headquarters")

    def test_enrich_uses_posting_unit_for_sort_fields(self) -> None:
        hit = enrich_hit_display(
            {
                "bureau_code": "sta",
                "org_level": "headquarters",
                "current": {
                    "unit": "国家税务总局贵州省税务局",
                    "title": "局长",
                    "is_current": True,
                },
            }
        )
        self.assertEqual(hit["org_level"], "province")
        self.assertEqual(hit["unit_display"], "贵州省")
        self.assertEqual(hit["unit_sort_key"], "贵州省")

        xiamen = enrich_hit_display(
            {
                "bureau_code": "sta",
                "org_level": "headquarters",
                "current": {
                    "unit": "国家税务总局厦门市税务局",
                    "title": "局长",
                    "is_current": True,
                },
            }
        )
        self.assertEqual(xiamen["org_level"], "city")
        self.assertEqual(xiamen["unit_display"], "厦门市")
        self.assertNotEqual(xiamen.get("unit_category"), "internal")

    def test_headquarters_org_bucket_puts_chief_first(self) -> None:
        from tax_platform.search.display import headquarters_org_bucket

        chief = enrich_hit_display(
            {
                "bureau_code": "sta",
                "org_level": "headquarters",
                "current": {
                    "unit": "国家税务总局",
                    "department": None,
                    "title": "局长",
                    "is_current": True,
                },
            }
        )
        dept_head = enrich_hit_display(
            {
                "bureau_code": "sta",
                "org_level": "headquarters",
                "current": {
                    "unit": "国家税务总局",
                    "department": "党建工作局",
                    "title": "局长",
                    "is_current": True,
                },
            }
        )
        college = enrich_hit_display(
            {
                "bureau_code": "sta",
                "org_level": "headquarters",
                "current": {
                    "unit": "国家税务总局税务干部学院",
                    "department": "税务干部学院",
                    "title": "局长",
                    "is_current": True,
                },
            }
        )
        self.assertEqual(headquarters_org_bucket(chief), 0)
        self.assertEqual(headquarters_org_bucket(dept_head), 1)
        self.assertEqual(headquarters_org_bucket(college), 2)
        self.assertLess(headquarters_org_bucket(chief), headquarters_org_bucket(dept_head))
        self.assertLess(headquarters_org_bucket(dept_head), headquarters_org_bucket(college))


if __name__ == "__main__":
    unittest.main()
