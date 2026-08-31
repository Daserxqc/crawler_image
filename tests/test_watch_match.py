from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.accounts.watch_match import (
    build_department_target_id,
    build_post_target_id,
    event_matches_watch,
)


class WatchMatchTests(unittest.TestCase):
    def test_bureau_watch(self) -> None:
        event = {
            "bureau_code": "pdtax",
            "person_name": "甲",
            "department_raw": "货劳科",
            "title_raw": "科长",
        }
        watch = {"target_type": "bureau", "target_id": "pdtax"}
        self.assertTrue(event_matches_watch(event, watch))

    def test_department_watch(self) -> None:
        event = {
            "bureau_code": "pdtax",
            "person_name": "乙",
            "department_raw": "政策法规处",
            "title_raw": "副处长",
        }
        watch = {
            "target_type": "department",
            "target_id": build_department_target_id("pdtax", "政策法规处"),
        }
        self.assertTrue(event_matches_watch(event, watch))
        self.assertFalse(
            event_matches_watch(
                event,
                {"target_type": "department", "target_id": build_department_target_id("pdtax", "货劳科")},
            )
        )

    def test_post_watch_with_title(self) -> None:
        event = {
            "bureau_code": "shanghai",
            "person_name": "丙",
            "department_raw": "政策法规处",
            "title_raw": "处长",
        }
        watch = {
            "target_type": "post",
            "target_id": build_post_target_id("shanghai", "政策法规处", "处长"),
        }
        self.assertTrue(event_matches_watch(event, watch))
        self.assertFalse(
            event_matches_watch(
                event,
                {"target_type": "post", "target_id": build_post_target_id("shanghai", "政策法规处", "副处长")},
            )
        )


if __name__ == "__main__":
    unittest.main()
