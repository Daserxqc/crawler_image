# -*- coding: utf-8 -*-
"""Rebuild org_posts / org_post_tenures and sync person identities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store.identity import sync_person_identities
from tax_platform.store.posts import rebuild_org_posts
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    args = parser.parse_args()
    conn = connect(args.db)
    n_id = sync_person_identities(conn, force=True)
    stats = rebuild_org_posts(conn)
    conn.commit()
    conn.close()
    print(f"identities synced={n_id}; posts={stats}")


if __name__ == "__main__":
    main()
