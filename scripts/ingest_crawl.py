from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.store import ingest_appointment_results, ingest_leader_results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest crawl JSON into SQLite.")
    parser.add_argument("--appointments", default="output/shanghai_appointments.json")
    parser.add_argument("--leaders", default="output/shanghai_leaders.json")
    parser.add_argument("--db", default="output/tax_hr.db")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    appts = json.loads(Path(args.appointments).read_text(encoding="utf-8"))
    leaders = json.loads(Path(args.leaders).read_text(encoding="utf-8"))
    event_count = ingest_appointment_results(appts)
    leader_count = ingest_leader_results(leaders)
    print(json.dumps({"events": event_count, "leaders": leader_count, "db": str(Path(args.db).resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
