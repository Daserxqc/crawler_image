from __future__ import annotations

import json
import sqlite3
from typing import Any

from tax_platform.config.sites import get_site
from tax_platform.store.schema import connect
from tax_platform.store.tenure import build_current_from_history, enrich_history_rows


def person_id(bureau_code: str, name: str) -> str:
    return f"{bureau_code}:{name}"


def ingest_appointment_results(
    results: list[dict[str, Any]] | dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    owns = conn is None
    db = conn or connect()
    count = 0
    payload = results if isinstance(results, list) else [results]
    for site_result in payload:
        bureau = site_result["bureau"]
        notice_by_url = {notice["source_url"]: notice for notice in site_result.get("notices", [])}
        # Persist list/detail notices even when body fetch or event parse failed.
        for notice in site_result.get("notices", []):
            _upsert_notice(db, {**notice, "bureau_code": notice.get("bureau_code") or bureau})
        for event in site_result.get("events", []):
            notice = notice_by_url.get(event.get("source_url"))
            if notice is None:
                continue
            notice = {**notice, "bureau_code": notice.get("bureau_code") or bureau}
            notice_id = _upsert_notice(db, notice)
            if _insert_event(db, bureau, notice_id, event):
                _upsert_person_from_event(db, bureau, event)
                count += 1
    if owns:
        db.commit()
        db.close()
    return count


def ingest_leader_results(
    results: list[dict[str, Any]] | dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    owns = conn is None
    db = conn or connect()
    count = 0
    payload = results if isinstance(results, list) else [results]
    for site_result in payload:
        bureau = site_result["bureau"]
        for leader in site_result.get("leaders", []):
            _upsert_leader(db, bureau, leader)
            _upsert_person_from_leader(db, bureau, leader)
            count += 1
    if owns:
        db.commit()
        db.close()
    return count


def get_person_profile(pid: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    owns = conn is None
    db = conn or connect()
    person = db.execute("SELECT * FROM persons WHERE id = ?", (pid,)).fetchone()
    if person is None:
        if owns:
            db.close()
        return None

    bureau_code = person["bureau_code"]
    name = person["name"]
    try:
        site = get_site(bureau_code)
        region = site.region
        level_label = _level_label(site.level)
    except KeyError:
        region = None
        level_label = None

    leader = db.execute(
        "SELECT * FROM leader_duties WHERE bureau_code = ? AND person_name = ? ORDER BY id DESC LIMIT 1",
        (bureau_code, name),
    ).fetchone()

    events = db.execute(
        """
        SELECT * FROM appointment_events
        WHERE bureau_code = ? AND person_name = ?
        ORDER BY COALESCE(effective_on, '') DESC, id DESC
        """,
        (bureau_code, name),
    ).fetchall()

    history = enrich_history_rows(list(events), bureau_code=bureau_code)
    leader_dict = dict(leader) if leader is not None else None
    current = build_current_from_history(
        history,
        leader=leader_dict,
        person_title=person["title_current"],
    )

    profile = {
        "id": pid,
        "name": name,
        "bureau_code": bureau_code,
        "region": region,
        "tags": _build_tags(current, level_label),
        "current": current,
        "history": history,
        "leader_intro_url": leader["source_url"] if leader else None,
    }
    if owns:
        db.close()
    return profile


def list_persons(
    *,
    bureau_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """List everyone in the database (no need to query one name at a time)."""
    owns = conn is None
    db = conn or connect()
    if bureau_code:
        rows = db.execute(
            "SELECT * FROM persons WHERE bureau_code = ? ORDER BY bureau_code, name",
            (bureau_code,),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM persons ORDER BY bureau_code, name").fetchall()
    result = [
        {
            "id": row["id"],
            "name": row["name"],
            "bureau_code": row["bureau_code"],
            "title_current": row["title_current"],
        }
        for row in rows
    ]
    if owns:
        db.close()
    return result


def export_profiles(
    *,
    bureau_code: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Build full profiles (with notice links) for everyone in the database."""
    people = list_persons(bureau_code=bureau_code, conn=conn)
    owns = conn is None
    db = conn or connect()
    profiles = []
    for person in people:
        profile = get_person_profile(person["id"], conn=db)
        if profile:
            profiles.append(profile)
    if owns:
        db.close()
    return profiles


def _upsert_notice(db: sqlite3.Connection, notice: dict[str, Any]) -> int:
    existing = db.execute(
        "SELECT id, raw_text FROM notices WHERE source_url = ?",
        (notice["source_url"],),
    ).fetchone()
    if existing:
        nid = int(existing["id"])
        new_raw = notice.get("raw_text")
        old_raw = existing["raw_text"]
        # Prefer longer / newly filled body when re-crawling the same URL.
        if new_raw and (not old_raw or len(str(new_raw)) >= len(str(old_raw))):
            raw_text = new_raw
        else:
            raw_text = old_raw
        db.execute(
            """
            UPDATE notices SET
                bureau_code = COALESCE(?, bureau_code),
                title = COALESCE(?, title),
                published_at = COALESCE(?, published_at),
                doc_no = COALESCE(?, doc_no),
                issuer = COALESCE(?, issuer),
                issued_on = COALESCE(?, issued_on),
                raw_text = ?
            WHERE id = ?
            """,
            (
                notice.get("bureau_code"),
                notice.get("title"),
                notice.get("published_at"),
                notice.get("doc_no"),
                notice.get("issuer"),
                notice.get("issued_on"),
                raw_text,
                nid,
            ),
        )
        return nid
    cursor = db.execute(
        """
        INSERT INTO notices (bureau_code, title, source_url, published_at, doc_no, issuer, issued_on, raw_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notice.get("bureau_code"),
            notice.get("title"),
            notice.get("source_url"),
            notice.get("published_at"),
            notice.get("doc_no"),
            notice.get("issuer"),
            notice.get("issued_on"),
            notice.get("raw_text"),
        ),
    )
    return int(cursor.lastrowid)


def _insert_event(
    db: sqlite3.Connection,
    bureau_code: str,
    notice_id: int,
    event: dict[str, Any],
) -> bool:
    try:
        db.execute(
            """
            INSERT INTO appointment_events (
                notice_id, bureau_code, person_name, action, bureau_name, department_raw,
                title_raw, probation_years, effective_on, notice_title, raw_clause, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notice_id,
                bureau_code,
                event.get("person_name"),
                event.get("action"),
                event.get("bureau_name"),
                event.get("department_raw"),
                event.get("title_raw"),
                event.get("probation_years"),
                event.get("effective_on"),
                event.get("notice_title"),
                event.get("raw_clause"),
                event.get("source_url"),
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def _upsert_leader(db: sqlite3.Connection, bureau_code: str, leader: dict[str, Any]) -> None:
    db.execute(
        """
        INSERT INTO leader_duties (
            bureau_code, person_name, gender, ethnicity, title_raw, duty_summary,
            departments_json, source_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bureau_code, person_name, source_url) DO UPDATE SET
            gender=excluded.gender,
            ethnicity=excluded.ethnicity,
            title_raw=excluded.title_raw,
            duty_summary=excluded.duty_summary,
            departments_json=excluded.departments_json
        """,
        (
            bureau_code,
            leader.get("person_name"),
            leader.get("gender"),
            leader.get("ethnicity"),
            leader.get("title_raw"),
            leader.get("duty_summary"),
            json.dumps(leader.get("departments_raw") or [], ensure_ascii=False),
            leader.get("source_url"),
        ),
    )


def _upsert_person_from_event(db: sqlite3.Connection, bureau_code: str, event: dict[str, Any]) -> None:
    name = event.get("person_name")
    if not name:
        return
    pid = person_id(bureau_code, name)
    title = event.get("title_raw")
    db.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        VALUES (?, ?, ?, NULL, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            title_current=COALESCE(excluded.title_current, persons.title_current)
        """,
        (pid, name, bureau_code, title),
    )


def _upsert_person_from_leader(db: sqlite3.Connection, bureau_code: str, leader: dict[str, Any]) -> None:
    name = leader.get("person_name")
    if not name:
        return
    pid = person_id(bureau_code, name)
    db.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            gender=excluded.gender,
            title_current=excluded.title_current,
            source_leader_url=excluded.source_leader_url
        """,
        (pid, name, bureau_code, leader.get("gender"), leader.get("title_raw"), leader.get("source_url")),
    )


def _build_tags(current: dict[str, Any], level_label: str | None) -> list[str]:
    tags: list[str] = []
    if current.get("is_current"):
        tags.append("现任")
    if level_label:
        tags.append(level_label)
    return tags


def _level_label(level: str | None) -> str | None:
    mapping = {
        "headquarters": "总局层面",
        "province": "省局层面",
        "city": "市局层面",
        "district": "区局层面",
    }
    return mapping.get(level or "")
