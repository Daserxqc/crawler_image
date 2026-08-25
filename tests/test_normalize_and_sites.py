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
from tax_platform.normalize.title import normalize_title
from tax_platform.models.entities import OrgLevel


class SiteRegistryTests(unittest.TestCase):
    def test_national_registry_size(self) -> None:
        self.assertEqual(len(ALL_SITES), 48)  # 1 HQ + 30 省 + 17 上海
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

    def test_normalize_department_kind(self) -> None:
        dept = normalize_department("个人所得税处", org_level=OrgLevel.PROVINCE)
        self.assertIsNotNone(dept)
        assert dept is not None
        self.assertEqual(dept.kind.value, "chu")


if __name__ == "__main__":
    unittest.main()
