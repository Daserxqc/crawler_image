# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import ALL_SITES
from tax_platform.store.org_units import get_org_unit, list_org_units, sync_org_units_from_sites
from tax_platform.store.schema import connect


class OrgUnitsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_connect_syncs_org_units(self) -> None:
        n = self.conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0]
        self.assertEqual(n, len(ALL_SITES))
        shanghai = get_org_unit(self.conn, "shanghai")
        self.assertIsNotNone(shanghai)
        assert shanghai is not None
        self.assertEqual(shanghai["level"], "province")
        self.assertEqual(shanghai["region"], "上海市")

    def test_list_children(self) -> None:
        kids = list_org_units(self.conn, parent_code="shanghai")
        codes = {k["code"] for k in kids}
        self.assertIn("pdtax", codes)
        self.assertIn("dyjcj", codes)

    def test_force_resync(self) -> None:
        self.conn.execute("DELETE FROM org_units WHERE code = 'pdtax'")
        self.conn.commit()
        sync_org_units_from_sites(self.conn, force=True)
        self.conn.commit()
        self.assertIsNotNone(get_org_unit(self.conn, "pdtax"))


if __name__ == "__main__":
    unittest.main()
