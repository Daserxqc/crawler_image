# -*- coding: utf-8 -*-
"""Force-sync org_units from site registry into SQLite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import ALL_SITES, reload_sites
from tax_platform.store.org_units import sync_org_units_from_sites
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    args = parser.parse_args()
    reload_sites()
    conn = connect(args.db)
    n = sync_org_units_from_sites(conn, force=True)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0]
    conn.close()
    print(f"synced {n} sites from registry; org_units rows={total} (registry={len(ALL_SITES)})")


if __name__ == "__main__":
    main()
