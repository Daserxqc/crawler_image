from __future__ import annotations

import json
import sqlite3
from typing import Any

from tax_platform.config.sites import get_site
from tax_platform.crawler.appointment_list import is_appointment_list_url
from tax_platform.crawler.notice_url import normalize_notice_source_url, notice_source_url_variants
from tax_platform.crawler.text_clean import normalize_doc_no, normalize_notice_title
from tax_platform.store.events import sync_events_for_source, upsert_appointment_event
from tax_platform.store.schema import connect
from tax_platform.store.tenure import (
    build_current_from_history,
    enrich_history_rows,
    recompute_persons,
)


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
    touched: set[tuple[str, str]] = set()
    payload = results if isinstance(results, list) else [results]
    for site_result in payload:
        bureau = site_result["bureau"]
        notice_by_url = {notice["source_url"]: notice for notice in site_result.get("notices", [])}
        events_by_url: dict[str, list[dict[str, Any]]] = {}
        for event in site_result.get("events", []):
            url = event.get("source_url")
            if not url:
                continue
            events_by_url.setdefault(url, []).append(event)

        # Persist list/detail notices even when body fetch or event parse failed.
        for notice in site_result.get("notices", []):
            notice = {**notice, "bureau_code": notice.get("bureau_code") or bureau}
            url = notice.get("source_url") or ""
            if is_appointment_list_url(url):
                continue
            notice_id, body_changed = _upsert_notice(db, notice)
            url = normalize_notice_source_url(notice.get("source_url")) or notice["source_url"]
            events: list[dict[str, Any]] = []
            for candidate in notice_source_url_variants(url):
                events.extend(events_by_url.pop(candidate, []))
            for event in events:
                event["source_url"] = url
            if body_changed and events:
                stats = sync_events_for_source(
                    db,
                    notice_id=notice_id,
                    bureau_code=bureau,
                    source_url=url,
                    events=events,
                    drop_orphans=True,
                )
                count += stats["inserted"]
                for ev in events:
                    name = ev.get("person_name")
                    if name:
                        _ensure_person_stub(db, bureau, name)
                        touched.add((bureau, name))
            elif events:
                # Body unchanged: insert new events only (update matching keys).
                for event in events:
                    _, inserted = upsert_appointment_event(
                        db,
                        notice_id=notice_id,
                        bureau_code=bureau,
                        event=event,
                    )
                    if inserted:
                        count += 1
                    name = event.get("person_name")
                    if name:
                        _ensure_person_stub(db, bureau, name)
                        touched.add((bureau, name))

        # Events whose notice was missing from the notices list.
        for url, events in events_by_url.items():
            notice = notice_by_url.get(url)
            if notice is None:
                continue
            notice = {**notice, "bureau_code": notice.get("bureau_code") or bureau}
            notice_id, _ = _upsert_notice(db, notice)
            for event in events:
                _, inserted = upsert_appointment_event(
                    db,
                    notice_id=notice_id,
                    bureau_code=bureau,
                    event=event,
                )
                if inserted:
                    count += 1
                name = event.get("person_name")
                if name:
                    _ensure_person_stub(db, bureau, name)
                    touched.add((bureau, name))

    if touched:
        recompute_persons(db, touched)
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
    touched: set[tuple[str, str]] = set()
    payload = results if isinstance(results, list) else [results]
    for site_result in payload:
        bureau = site_result["bureau"]
        for leader in site_result.get("leaders", []):
            _upsert_leader(db, bureau, leader)
            name = leader.get("person_name")
            if name:
                _ensure_person_stub(db, bureau, name, gender=leader.get("gender"), source_leader_url=leader.get("source_url"))
                touched.add((bureau, name))
            count += 1
    if touched:
        recompute_persons(db, touched)
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
    site = None
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
    # Optional: same-name events from other bureaus (e.g. city leader appointed
    # in a province notice). Never pull a different org-level namesake — that
    # turns 津南「王海勇」into 总局所得税司副司长.
    if not events:
        try:
            local_level = get_site(bureau_code).level
        except KeyError:
            local_level = None
        foreign = db.execute(
            """
            SELECT * FROM appointment_events
            WHERE person_name = ? AND bureau_code != ?
            ORDER BY COALESCE(effective_on, '') DESC, id DESC
            """,
            (name, bureau_code),
        ).fetchall()
        events = []
        for row in foreign:
            try:
                if get_site(row["bureau_code"]).level != local_level:
                    continue
            except KeyError:
                continue
            events.append(row)

    history = enrich_history_rows(list(events), bureau_code=bureau_code)
    leader_dict = dict(leader) if leader is not None else None
    current = build_current_from_history(
        history,
        leader=leader_dict,
        person_title=person["title_current"],
        profile_bureau_code=bureau_code,
    )

    from tax_platform.store.identity import appearances_for_identity, resolve_identity_id

    identity_id = None
    if "identity_id" in person.keys():
        identity_id = person["identity_id"]
    if not identity_id:
        identity_id = resolve_identity_id(db, bureau_code=bureau_code, name=name)
    appearances = appearances_for_identity(db, identity_id) if identity_id else []

    profile = {
        "id": pid,
        "name": name,
        "bureau_code": bureau_code,
        "bureau_name": site.name if site else bureau_code,
        "org_level": site.level if site else None,
        "region": region,
        "identity_id": identity_id,
        "appearances": appearances,
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
            "is_current": bool(row["is_current"]) if "is_current" in row.keys() else None,
            "department_current": row["department_current"] if "department_current" in row.keys() else None,
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


def known_notice_urls(*, conn: sqlite3.Connection | None = None) -> set[str]:
    owns = conn is None
    db = conn or connect()
    urls = {row[0] for row in db.execute("SELECT source_url FROM notices")}
    if owns:
        db.close()
    return urls


def _upsert_notice(db: sqlite3.Connection, notice: dict[str, Any]) -> tuple[int, bool]:
    """Upsert notice. Returns ``(notice_id, body_changed)``."""
    title = normalize_notice_title(notice.get("title")) or notice.get("title")
    doc_no = normalize_doc_no(notice.get("doc_no"))
    source_url = normalize_notice_source_url(notice.get("source_url")) or (notice.get("source_url") or "")
    notice = {**notice, "source_url": source_url}
    existing = None
    for candidate in notice_source_url_variants(source_url):
        existing = db.execute(
            "SELECT id, raw_text, doc_no, source_url FROM notices WHERE source_url = ?",
            (candidate,),
        ).fetchone()
        if existing:
            break
    if existing:
        nid = int(existing["id"])
        new_raw = notice.get("raw_text")
        old_raw = existing["raw_text"]
        body_changed = False
        if new_raw and (not old_raw or len(str(new_raw)) > len(str(old_raw))):
            raw_text = new_raw
            body_changed = True
        elif new_raw and old_raw and str(new_raw) != str(old_raw) and len(str(new_raw)) >= len(str(old_raw)):
            raw_text = new_raw
            body_changed = str(new_raw) != str(old_raw)
        else:
            raw_text = old_raw
        # Prefer freshly normalized doc_no; also scrub a previously stored chrome blob.
        stored_doc = doc_no if doc_no is not None else normalize_doc_no(existing["doc_no"])
        db.execute(
            """
            UPDATE notices SET
                bureau_code = COALESCE(?, bureau_code),
                title = COALESCE(?, title),
                source_url = ?,
                published_at = COALESCE(?, published_at),
                doc_no = ?,
                issuer = COALESCE(?, issuer),
                issued_on = COALESCE(?, issued_on),
                raw_text = ?
            WHERE id = ?
            """,
            (
                notice.get("bureau_code"),
                title,
                source_url,
                notice.get("published_at"),
                stored_doc,
                notice.get("issuer"),
                notice.get("issued_on"),
                raw_text,
                nid,
            ),
        )
        return nid, body_changed
    cursor = db.execute(
        """
        INSERT INTO notices (bureau_code, title, source_url, published_at, doc_no, issuer, issued_on, raw_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notice.get("bureau_code"),
            title,
            source_url,
            notice.get("published_at"),
            doc_no,
            notice.get("issuer"),
            notice.get("issued_on"),
            notice.get("raw_text"),
        ),
    )
    # Treat first insert as body available for sync.
    return int(cursor.lastrowid), bool(notice.get("raw_text"))


def _ensure_person_stub(
    db: sqlite3.Connection,
    bureau_code: str,
    name: str,
    *,
    gender: str | None = None,
    source_leader_url: str | None = None,
) -> None:
    pid = person_id(bureau_code, name)
    db.execute(
        """
        INSERT INTO persons (id, name, bureau_code, gender, title_current, source_leader_url)
        VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(id) DO UPDATE SET
            gender=COALESCE(excluded.gender, persons.gender),
            source_leader_url=COALESCE(excluded.source_leader_url, persons.source_leader_url)
        """,
        (pid, name, bureau_code, gender, source_leader_url),
    )


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
