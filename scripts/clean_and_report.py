"""Re-ingest all province crawl JSON, light-clean names, write quality report."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites_provinces import PROVINCE_SUBDOMAINS
from tax_platform.normalize.title import normalize_title
from tax_platform.store import ingest_appointment_results, ingest_leader_results
from tax_platform.store.schema import connect

DB = ROOT / "output" / "tax_hr.db"
PROV = ROOT / "output" / "provinces"
REPORT = ROOT / "output" / "nationwide_clean_report.txt"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _file_counts(code: str) -> tuple[int, int]:
    lf = PROV / f"{code}_leaders.json"
    af = PROV / f"{code}_appointments.json"
    n = e = 0
    if lf.exists():
        d = _load(lf)
        leaders = d if isinstance(d, list) else d.get("leaders") or []
        n = len(leaders or [])
    if af.exists():
        d = _load(af)
        if isinstance(d, dict):
            e = len(d.get("events") or [])
            if not e and d.get("notices"):
                e = sum(len(x.get("events") or []) for x in d["notices"] if isinstance(x, dict))
        elif isinstance(d, list):
            e = len(d)
    return n, e


def reingest_all(conn: sqlite3.Connection) -> None:
    for code, _name, _ in PROVINCE_SUBDOMAINS:
        lf = PROV / f"{code}_leaders.json"
        af = PROV / f"{code}_appointments.json"
        if lf.exists():
            ingest_leader_results(_load(lf), conn=conn)
        if af.exists():
            ingest_appointment_results(_load(af), conn=conn)


def clean_names(conn: sqlite3.Connection) -> dict[str, int]:
    changed = {"leader_space": 0, "event_space": 0, "empty_name_events": 0, "deduped_events": 0}
    for i, name in conn.execute("SELECT id, person_name FROM leader_duties"):
        cleaned = "".join(str(name).split())
        if cleaned != name:
            conn.execute("UPDATE leader_duties SET person_name=? WHERE id=?", (cleaned, i))
            changed["leader_space"] += 1
    for i, name in conn.execute("SELECT id, person_name FROM appointment_events"):
        cleaned = "".join(str(name).split())
        if not cleaned:
            conn.execute("DELETE FROM appointment_events WHERE id=?", (i,))
            changed["empty_name_events"] += 1
            continue
        if cleaned != name:
            conn.execute(
                "UPDATE appointment_events SET person_name=? WHERE id=?",
                (cleaned, i),
            )
            changed["event_space"] += 1
    before = conn.execute("SELECT COUNT(*) FROM appointment_events").fetchone()[0]
    conn.execute(
        """
        DELETE FROM appointment_events
        WHERE id NOT IN (
            SELECT MIN(id) FROM appointment_events
            GROUP BY source_url, person_name, action, COALESCE(raw_clause, '')
        )
        """
    )
    after = conn.execute("SELECT COUNT(*) FROM appointment_events").fetchone()[0]
    changed["deduped_events"] = before - after

    # Rebuild persons from appointments first (keep appointment-only people), then leaders.
    conn.execute("DELETE FROM persons")
    conn.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        SELECT
            bureau_code || ':' || person_name,
            person_name,
            bureau_code,
            NULL,
            (
                SELECT e2.title_raw FROM appointment_events e2
                WHERE e2.bureau_code = e.bureau_code AND e2.person_name = e.person_name
                ORDER BY COALESCE(e2.effective_on, '') DESC, e2.id DESC
                LIMIT 1
            ),
            NULL
        FROM (
            SELECT DISTINCT bureau_code, person_name FROM appointment_events
        ) e
        """
    )
    conn.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        SELECT
            bureau_code || ':' || person_name,
            person_name,
            bureau_code,
            gender,
            title_raw,
            source_url
        FROM leader_duties
        WHERE person_name IS NOT NULL AND TRIM(person_name) != ''
        ON CONFLICT(id) DO UPDATE SET
            gender=COALESCE(excluded.gender, persons.gender),
            title_current=COALESCE(excluded.title_current, persons.title_current),
            source_leader_url=COALESCE(excluded.source_leader_url, persons.source_leader_url)
        """
    )
    return changed


def build_report(conn: sqlite3.Connection, cleaned: dict[str, int]) -> str:
    lines: list[str] = []
    lines.append("CLEANED " + json.dumps(cleaned, ensure_ascii=False))
    lines.append("")
    lines.append("=== Coverage (province JSON) ===")
    both = half = zero = 0
    for code, name, _ in PROVINCE_SUBDOMAINS:
        n, e = _file_counts(code)
        if n and e:
            both += 1
            flag = "OK"
        elif n or e:
            half += 1
            flag = "HALF"
        else:
            zero += 1
            flag = "ZERO"
        thin = []
        if 0 < n < 3:
            thin.append(f"L={n}")
        if 0 < e < 5:
            thin.append(f"E={e}")
        extra = (" thin:" + ",".join(thin)) if thin else ""
        lines.append(f"{flag}\t{code}\t{name}\tL={n}\tE={e}{extra}")
    lines.append(f"SUMMARY both={both} half={half} zero={zero} / 30")

    lines.append("")
    lines.append("=== SQLite totals ===")
    for label, q in [
        ("persons", "SELECT COUNT(*) FROM persons"),
        ("leader_duties", "SELECT COUNT(*) FROM leader_duties"),
        ("notices", "SELECT COUNT(*) FROM notices"),
        ("appointment_events", "SELECT COUNT(*) FROM appointment_events"),
        ("bureaus_with_leaders", "SELECT COUNT(DISTINCT bureau_code) FROM leader_duties"),
        ("bureaus_with_events", "SELECT COUNT(DISTINCT bureau_code) FROM appointment_events"),
    ]:
        lines.append(f"{label}\t{conn.execute(q).fetchone()[0]}")

    lines.append("")
    lines.append("=== Leaders with empty title_raw ===")
    miss = conn.execute(
        """
        SELECT bureau_code, COUNT(*) FROM leader_duties
        WHERE title_raw IS NULL OR TRIM(title_raw)=''
        GROUP BY bureau_code ORDER BY 2 DESC
        """
    ).fetchall()
    lines.extend(f"{b}\t{c}" for b, c in miss) if miss else lines.append("(none)")

    lines.append("")
    lines.append("=== Top title_raw -> normalize_title ===")
    for raw, c in conn.execute(
        """
        SELECT title_raw, COUNT(*) c FROM leader_duties
        WHERE title_raw IS NOT NULL AND TRIM(title_raw)!=''
        GROUP BY title_raw ORDER BY c DESC LIMIT 25
        """
    ):
        nt = normalize_title(raw)
        canon = nt.canonical if nt else ""
        lines.append(f"{c}\t{raw}\t=>\t{canon}")

    lines.append("")
    lines.append("=== Per-bureau DB ===")
    by: dict[str, dict[str, int]] = defaultdict(lambda: {"L": 0, "E": 0})
    for b, c in conn.execute(
        "SELECT bureau_code, COUNT(*) FROM leader_duties GROUP BY bureau_code"
    ):
        by[b]["L"] = c
    for b, c in conn.execute(
        "SELECT bureau_code, COUNT(*) FROM appointment_events GROUP BY bureau_code"
    ):
        by[b]["E"] = c
    for code, name, _ in PROVINCE_SUBDOMAINS:
        lines.append(f"{code}\t{name}\tL={by[code]['L']}\tE={by[code]['E']}")

    return "\n".join(lines)


def main() -> None:
    conn = connect(DB)
    reingest_all(conn)
    cleaned = clean_names(conn)
    conn.commit()
    report = build_report(conn, cleaned)
    REPORT.write_text(report, encoding="utf-8")
    conn.close()
    print(report)
    print(f"\nWrote {REPORT}")


if __name__ == "__main__":
    main()
