"""Repair appointment_events: backfill bureau_name, dedupe semantic twins."""

from __future__ import annotations

import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from tax_platform.crawler.appointment_clauses import (
    APPOINT_AS_RE,
    APPOINT_GUAZHI_RE,
    APPOINT_RE,
    APPOINT_RENYONG_RE,
    APPOINT_TONGZHI_DANREN_RE,
    APPOINT_ZHENGSHI_RENYONG_RE,
    CONFIRM_APPOINT_RE,
    ROSTER_APPOINT_RE,
    split_post,
)
from tax_platform.normalize.person import is_plausible_person_name

_BUREAU_SNIPPET = re.compile(
    r"(国家税务总局[^；。，,（）()\s]+?(?:税务局|税务分局|稽查局))"
)
_LOCAL_BUREAU = re.compile(
    r"([\u4e00-\u9fa5]{2,40}?(?:税务局|税务分局|稽查局))"
)


def _clean_bureau_name(name: str) -> str:
    return re.sub(r"\s+", "", name)


def normalize_source_url(url: str | None) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u if "://" in u else f"http://{u}")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}"


def event_semantic_key(row: dict[str, Any] | sqlite3.Row) -> tuple[str, ...]:
    """Same person/post/day under one bureau_code = one logical event."""
    return (
        str(row["bureau_code"] or ""),
        str(row["person_name"] or ""),
        str(row["action"] or ""),
        str(row["department_raw"] or ""),
        str(row["title_raw"] or ""),
        str(row["effective_on"] or ""),
    )


def infer_bureau_name_from_clause(
    clause: str | None,
    *,
    person_name: str | None = None,
) -> str | None:
    text = (clause or "").strip()
    if not text:
        return None

    name = (person_name or "").strip()
    if not name or len(name) > 6:
        m = APPOINT_RE.search(text) or APPOINT_AS_RE.search(text)
        if m and m.groupdict().get("name"):
            name = m.group("name")

    patterns = (
        APPOINT_AS_RE,
        APPOINT_ZHENGSHI_RENYONG_RE,
        APPOINT_RENYONG_RE,
        APPOINT_GUAZHI_RE,
        APPOINT_TONGZHI_DANREN_RE,
        CONFIRM_APPOINT_RE,
        APPOINT_RE,
        ROSTER_APPOINT_RE,
    )
    for pat in patterns:
        for match in pat.finditer(text):
            if name and "name" in match.groupdict():
                if match.group("name") != name:
                    continue
            post = match.group("post")
            bureau, _, _ = split_post(post)
            if bureau and ("税务局" in bureau or "稽查局" in bureau):
                return _clean_bureau_name(bureau)

    hit = _BUREAU_SNIPPET.search(text)
    if hit:
        return _clean_bureau_name(hit.group(1))
    for hit in _LOCAL_BUREAU.finditer(text):
        candidate = _clean_bureau_name(hit.group(1))
        if "税务局" in candidate or "稽查局" in candidate:
            if candidate.startswith("国家税务总局") or len(candidate) >= 6:
                return candidate
    return None


def _row_score(row: sqlite3.Row) -> tuple[int, int, int]:
    """Prefer rows with bureau_name and richer clause; lower id wins."""
    has_unit = 1 if (row["bureau_name"] or "").strip() else 0
    clause_len = len(row["raw_clause"] or "")
    return (has_unit, clause_len, -int(row["id"]))


def backfill_bureau_names(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    rows = conn.execute(
        """
        SELECT id, person_name, bureau_name, raw_clause
        FROM appointment_events
        WHERE bureau_name IS NULL OR TRIM(bureau_name) = ''
        """
    ).fetchall()
    updated = 0
    for row in rows:
        inferred = infer_bureau_name_from_clause(
            row["raw_clause"],
            person_name=row["person_name"],
        )
        if not inferred:
            continue
        updated += 1
        if not dry_run:
            conn.execute(
                "UPDATE appointment_events SET bureau_name = ? WHERE id = ?",
                (inferred, int(row["id"])),
            )
    if not dry_run and updated:
        conn.commit()
    return updated


def dedupe_appointment_events(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    """Drop semantic duplicates; keep the richest row per key."""
    rows = conn.execute(
        """
        SELECT id, bureau_code, person_name, action, department_raw, title_raw,
               effective_on, bureau_name, raw_clause, source_url
        FROM appointment_events
        ORDER BY id ASC
        """
    ).fetchall()
    best_by_key: dict[tuple[str, ...], sqlite3.Row] = {}
    for row in rows:
        key = event_semantic_key(row)
        prev = best_by_key.get(key)
        if prev is None or _row_score(row) > _row_score(prev):
            best_by_key[key] = row

    drop_ids: list[int] = []
    for row in rows:
        key = event_semantic_key(row)
        keeper = best_by_key[key]
        if int(row["id"]) != int(keeper["id"]):
            drop_ids.append(int(row["id"]))

    if not drop_ids:
        return 0

    if dry_run:
        return len(drop_ids)

    # Merge bureau_name onto keeper when missing.
    for key, keeper in best_by_key.items():
        if (keeper["bureau_name"] or "").strip():
            continue
        for row in rows:
            if event_semantic_key(row) != key:
                continue
            if (row["bureau_name"] or "").strip():
                conn.execute(
                    "UPDATE appointment_events SET bureau_name = ? WHERE id = ?",
                    (row["bureau_name"], int(keeper["id"])),
                )
                break

    placeholders = ",".join("?" * len(drop_ids))
    conn.execute(
        f"DELETE FROM appointment_events WHERE id IN ({placeholders})",
        drop_ids,
    )
    conn.commit()
    return len(drop_ids)


def repair_misnamed_persons(conn: sqlite3.Connection, *, dry_run: bool = False) -> int:
    """Fix rows where parser stored a title/rank fragment as person_name."""
    rows = conn.execute(
        """
        SELECT id, person_name, raw_clause
        FROM appointment_events
        WHERE person_name IS NOT NULL AND TRIM(person_name) != ''
        """
    ).fetchall()
    fixed = 0
    for row in rows:
        if is_plausible_person_name(row["person_name"]):
            continue
        text = row["raw_clause"] or ""
        found = None
        for pat in (APPOINT_AS_RE, APPOINT_RE, ROSTER_APPOINT_RE):
            m = pat.search(text)
            if m and m.groupdict().get("name"):
                candidate = m.group("name")
                if is_plausible_person_name(candidate):
                    found = candidate
                    break
        if not found:
            continue
        fixed += 1
        if not dry_run:
            conn.execute(
                "UPDATE appointment_events SET person_name = ? WHERE id = ?",
                (found, int(row["id"])),
            )
    if not dry_run and fixed:
        conn.commit()
    return fixed


def repair_appointment_events(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict[str, int]:
    renamed = repair_misnamed_persons(conn, dry_run=dry_run)
    filled = backfill_bureau_names(conn, dry_run=dry_run)
    deduped = dedupe_appointment_events(conn, dry_run=dry_run)
    if not dry_run:
        filled += backfill_bureau_names(conn, dry_run=False)
    return {"person_renamed": renamed, "bureau_filled": filled, "deduped": deduped}
