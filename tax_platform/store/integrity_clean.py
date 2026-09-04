"""Clean integrity noise: orphan bureau codes + STA→local event remap."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from tax_platform.search.display import _match_site_from_unit

# Discovery noise: leaders/persons stamped with path/col codes that are really
# the provincial bureau. All current rows already exist under the target code.
ORPHAN_BUREAU_REMAP: dict[str, str] = {
    "hunan_path_login": "hunan",
    "liaoning_col122": "liaoning",
    "liaoning_col1795": "liaoning",
    "liaoning_col2030": "liaoning",
    "liaoning_col226": "liaoning",
    "liaoning_col2623": "liaoning",
    "liaoning_col27": "liaoning",
    "liaoning_col51": "liaoning",
}

BUREAU_LEVEL_DEPT = "本局"


def resolve_event_posting_bureau(
    bureau_code: str,
    bureau_name: str | None,
) -> str:
    """Prefer the registry site named in ``bureau_name`` when STA appointed locally."""
    code = (bureau_code or "").strip()
    matched = _match_site_from_unit((bureau_name or "").strip())
    if matched is None:
        return code
    if code == "sta" and matched.code != "sta":
        return matched.code
    return code


def purge_orphan_bureau_codes(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete leaders/persons/anomalies/identities under known junk bureau codes."""
    report: dict[str, Any] = {"codes": {}, "dry_run": dry_run}
    for old_code, target in ORPHAN_BUREAU_REMAP.items():
        stats: dict[str, int] = {}
        for table, col in (
            ("leader_duties", "bureau_code"),
            ("persons", "bureau_code"),
            ("data_anomalies", "bureau_code"),
            ("appointment_events", "bureau_code"),
            ("notices", "bureau_code"),
            ("org_units", "code"),
        ):
            try:
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col}=?",
                    (old_code,),
                ).fetchone()[0]
            except sqlite3.Error:
                n = 0
            stats[table] = int(n)
            if n and not dry_run:
                conn.execute(f"DELETE FROM {table} WHERE {col}=?", (old_code,))

        # Name keys / identities tied to junk person ids.
        try:
            n_keys = conn.execute(
                "SELECT COUNT(*) FROM person_name_keys WHERE bureau_code=?",
                (old_code,),
            ).fetchone()[0]
        except sqlite3.Error:
            n_keys = 0
        stats["person_name_keys"] = int(n_keys)
        if n_keys and not dry_run:
            conn.execute(
                "DELETE FROM person_name_keys WHERE bureau_code=?",
                (old_code,),
            )

        stats["target"] = target  # type: ignore[assignment]
        report["codes"][old_code] = stats

    if not dry_run:
        conn.commit()
    return report


def remap_sta_local_events(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Move STA-crawled events that name a local bureau onto that bureau_code."""
    rows = conn.execute(
        """
        SELECT id, bureau_code, bureau_name, person_name
        FROM appointment_events
        WHERE bureau_code = 'sta'
        """
    ).fetchall()
    remapped = 0
    by_target: dict[str, int] = {}
    for row in rows:
        target = resolve_event_posting_bureau(row["bureau_code"], row["bureau_name"])
        if target == "sta":
            continue
        remapped += 1
        by_target[target] = by_target.get(target, 0) + 1
        if not dry_run:
            conn.execute(
                "UPDATE appointment_events SET bureau_code = ? WHERE id = ?",
                (target, int(row["id"])),
            )
    if not dry_run and remapped:
        conn.commit()
    return {"remapped": remapped, **{f"to_{k}": v for k, v in sorted(by_target.items())}}


def prune_persons_without_source(
    conn: sqlite3.Connection,
    *,
    bureau_codes: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    """Drop persons with neither events nor leader rows (after remaps)."""
    if bureau_codes:
        placeholders = ",".join("?" * len(bureau_codes))
        people = conn.execute(
            f"SELECT id, bureau_code, name FROM persons WHERE bureau_code IN ({placeholders})",
            bureau_codes,
        ).fetchall()
    else:
        people = conn.execute("SELECT id, bureau_code, name FROM persons").fetchall()

    drop_ids: list[str] = []
    for row in people:
        bureau = row["bureau_code"]
        name = row["name"]
        has_event = conn.execute(
            """
            SELECT 1 FROM appointment_events
            WHERE bureau_code = ? AND person_name = ? LIMIT 1
            """,
            (bureau, name),
        ).fetchone()
        if has_event:
            continue
        has_leader = conn.execute(
            """
            SELECT 1 FROM leader_duties
            WHERE bureau_code = ? AND person_name = ? LIMIT 1
            """,
            (bureau, name),
        ).fetchone()
        if has_leader:
            continue
        drop_ids.append(row["id"])

    if dry_run or not drop_ids:
        return len(drop_ids)

    for pid in drop_ids:
        conn.execute("DELETE FROM persons WHERE id = ?", (pid,))
    # Clean keys that point at removed appearances.
    conn.execute(
        """
        DELETE FROM person_name_keys
        WHERE NOT EXISTS (
            SELECT 1 FROM persons p
            WHERE p.bureau_code = person_name_keys.bureau_code
              AND p.name = person_name_keys.name
        )
        """
    )
    conn.commit()
    return len(drop_ids)


_TONGZHI_NAME_RE = re.compile(
    r"(?:任命|聘任|免去)(?P<full>[\u4e00-\u9fa5·]{2,4})同志"
)


def repair_tongzhi_truncated_names(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fix names truncated by old 「X同志为」 parser (周立渊 → 立渊).

    When ``raw_clause`` contains ``任命完整姓名同志`` and stored ``person_name``
    is a proper suffix of that full name, rename the event (and person row).
    """
    from tax_platform.normalize.person import is_plausible_person_name
    from tax_platform.store.ingest import person_id
    from tax_platform.store.tenure import recompute_persons

    rows = conn.execute(
        """
        SELECT id, bureau_code, person_name, raw_clause
        FROM appointment_events
        WHERE raw_clause IS NOT NULL AND TRIM(raw_clause) != ''
          AND person_name IS NOT NULL AND TRIM(person_name) != ''
        """
    ).fetchall()

    renames: list[tuple[int, str, str, str]] = []  # event_id, bureau, old, new
    for row in rows:
        old = (row["person_name"] or "").strip()
        clause = row["raw_clause"] or ""
        if not old or "同志" not in clause:
            continue
        best = None
        for match in _TONGZHI_NAME_RE.finditer(clause):
            full = (match.group("full") or "").strip()
            if (
                full
                and full != old
                and full.endswith(old)
                and len(full) > len(old)
                and is_plausible_person_name(full)
            ):
                if best is None or len(full) > len(best):
                    best = full
        if best:
            renames.append((row["id"], row["bureau_code"], old, best))

    touched: set[tuple[str, str]] = set()
    person_moves: dict[tuple[str, str], str] = {}  # (bureau, old) -> new
    for _eid, bureau, old, new in renames:
        person_moves[(bureau, old)] = new
        touched.add((bureau, old))
        touched.add((bureau, new))

    report: dict[str, Any] = {
        "dry_run": dry_run,
        "event_renames": len(renames),
        "person_renames": len(person_moves),
        "samples": [
            {"bureau": b, "from": o, "to": n}
            for (_i, b, o, n) in renames[:12]
        ],
    }
    if dry_run or not renames:
        return report

    for eid, _bureau, _old, new in renames:
        conn.execute(
            "UPDATE appointment_events SET person_name = ? WHERE id = ?",
            (new, eid),
        )

    for (bureau, old), new in person_moves.items():
        old_id = person_id(bureau, old)
        new_id = person_id(bureau, new)
        conn.execute(
            """
            UPDATE leader_duties SET person_name = ?
            WHERE bureau_code = ? AND person_name = ?
            """,
            (new, bureau, old),
        )
        existing = conn.execute(
            "SELECT id FROM persons WHERE id = ?", (new_id,)
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM persons WHERE id = ?", (old_id,))
        else:
            conn.execute(
                """
                UPDATE persons SET id = ?, name = ?
                WHERE id = ?
                """,
                (new_id, new, old_id),
            )
        # Drop empty shells left when UPDATE lost the race / partial recompute.
        conn.execute(
            """
            DELETE FROM persons
            WHERE id = ?
              AND NOT EXISTS (
                SELECT 1 FROM appointment_events e
                WHERE e.bureau_code = persons.bureau_code
                  AND e.person_name = persons.name
              )
              AND NOT EXISTS (
                SELECT 1 FROM leader_duties l
                WHERE l.bureau_code = persons.bureau_code
                  AND l.person_name = persons.name
              )
            """,
            (old_id,),
        )
        conn.execute(
            """
            DELETE FROM person_name_keys
            WHERE bureau_code = ? AND name = ?
            """,
            (bureau, old),
        )

    conn.commit()
    recompute_persons(conn, person_keys=touched)
    conn.commit()
    return report
