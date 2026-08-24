from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.schedule import DEFAULT_REFRESH_DAYS, refresh_days_for
from tax_platform.config.sites import get_site
from tax_platform.crawler.crawl_state import (
    CrawlRunRecord,
    is_site_due,
    load_crawl_state,
    mark_crawl_result,
    save_crawl_state,
    sites_due_for_crawl,
    state_key,
)


class ScheduleTests(unittest.TestCase):
    def test_default_cadence(self) -> None:
        self.assertEqual(DEFAULT_REFRESH_DAYS["headquarters"], 90)
        self.assertEqual(DEFAULT_REFRESH_DAYS["province"], 30)
        self.assertEqual(DEFAULT_REFRESH_DAYS["city"], 14)
        self.assertEqual(DEFAULT_REFRESH_DAYS["district"], 7)
        self.assertEqual(refresh_days_for("province"), 30)
        self.assertEqual(refresh_days_for("district", override=3), 3)

    def test_shanghai_levels(self) -> None:
        self.assertEqual(get_site("shanghai").level, "province")
        self.assertEqual(get_site("pdtax").level, "district")


class CrawlStateTests(unittest.TestCase):
    def test_never_crawled_is_due(self) -> None:
        site = get_site("pdtax")
        self.assertTrue(is_site_due(site, kind="leaders", records={}))

    def test_recent_success_is_not_due(self) -> None:
        site = get_site("pdtax")
        now = datetime(2026, 8, 24, tzinfo=timezone.utc)
        records = {
            state_key("leaders", "pdtax"): CrawlRunRecord(
                last_success_at=now - timedelta(days=2),
                last_attempt_at=now - timedelta(days=2),
                ok=True,
            )
        }
        self.assertFalse(is_site_due(site, kind="leaders", records=records, now=now))
        self.assertTrue(
            is_site_due(site, kind="leaders", records=records, now=now + timedelta(days=7))
        )

    def test_province_interval_longer_than_district(self) -> None:
        now = datetime(2026, 8, 24, tzinfo=timezone.utc)
        records = {
            state_key("leaders", "shanghai"): CrawlRunRecord(
                last_success_at=now - timedelta(days=20),
                ok=True,
            ),
            state_key("leaders", "pdtax"): CrawlRunRecord(
                last_success_at=now - timedelta(days=20),
                ok=True,
            ),
        }
        due_codes = {site.code for site in sites_due_for_crawl("leaders", records=records, now=now)}
        self.assertIn("pdtax", due_codes)
        self.assertNotIn("shanghai", due_codes)

    def test_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "crawl_state.json"
            records: dict[str, CrawlRunRecord] = {}
            mark_crawl_result(records, kind="leaders", site_code="pdtax", ok=True)
            save_crawl_state(records, path)
            loaded = load_crawl_state(path)
            self.assertIn(state_key("leaders", "pdtax"), loaded)
            self.assertTrue(loaded[state_key("leaders", "pdtax")].ok)


if __name__ == "__main__":
    unittest.main()
