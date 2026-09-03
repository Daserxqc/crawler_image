"""Clean integrity noise: orphan bureau codes + STA→local event remap."""

from __future__ import annotations

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
