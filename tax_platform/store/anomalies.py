"""PR6: anomaly detection and manual correction write-back."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.schema import connect

EVENT_PATCH_FIELDS = frozenset(
    {
        "person_name",
        "action",
        "bureau_name",
        "department_raw",
        "title_raw",
        "effective_on",
        "notice_title",
        "raw_clause",
    }
)
LEADER_PATCH_FIELDS = frozenset(
    {
        "person_name",
        "gender",
        "ethnicity",
        "title_raw",
        "duty_summary",
        "departments_json",
    }
)
PERSON_PATCH_FIELDS = frozenset({"name", "gender", "title_current", "source_leader_url"})

_NOISE_DEPT_RE = re.compile(r"(任命|免去|通知|决定|欢迎|试用期)")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def scan_anomalies(*, conn: sqlite3.Connection | None = None) -> dict[str, int]:
    """Rebuild open anomalies from current appointment/leader/person data.

    Keeps manually ``ignored`` / ``resolved`` rows that still match the same
    unique key, so re-scan does not reopen handled items.
    """
    owns = conn is None
    db = conn or connect()

    kept = {
        (r["kind"], r["target_type"], r["target_id"]): dict(r)
        for r in db.execute(
            "SELECT * FROM data_anomalies WHERE status IN ('ignored', 'resolved')"
        )
    }
    db.execute("DELETE FROM data_anomalies WHERE status = 'open'")

    found: list[dict[str, Any]] = []
    found.extend(_scan_events(db))
    found.extend(_scan_leaders(db))
    found.extend(_scan_same_name(db))
    found.extend(_scan_conflicts(db))

    inserted = 0
    skipped_handled = 0
    for item in found:
        key = (item["kind"], item["target_type"], item["target_id"])
        if key in kept:
            skipped_handled += 1
            continue
        db.execute(
            """
            INSERT INTO data_anomalies (
                kind, severity, target_type, target_id, bureau_code, person_name,
                message, evidence_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            ON CONFLICT(kind, target_type, target_id) DO UPDATE SET
                severity=excluded.severity,
                bureau_code=excluded.bureau_code,
                person_name=excluded.person_name,
                message=excluded.message,
                evidence_json=excluded.evidence_json,
                status='open',
                resolved_at=NULL
            """,
            (
                item["kind"],
                item["severity"],
                item["target_type"],
                item["target_id"],
                item.get("bureau_code"),
                item.get("person_name"),
                item["message"],
                json.dumps(item.get("evidence") or {}, ensure_ascii=False),
                _now(),
            ),
        )
        inserted += 1

    db.commit()
    open_count = db.execute(
        "SELECT count(*) FROM data_anomalies WHERE status='open'"
    ).fetchone()[0]
    if owns:
        db.close()
    return {
        "inserted_or_refreshed": inserted,
        "skipped_handled": skipped_handled,
        "open_count": int(open_count),
    }


def list_anomalies(
    *,
    status: str = "open",
    kind: str | None = None,
    bureau_code: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    where = " WHERE 1=1"
    args: list[Any] = []
    if status and status != "all":
        where += " AND status = ?"
        args.append(status)
    if kind:
        where += " AND kind = ?"
        args.append(kind)
    if bureau_code:
        where += " AND bureau_code = ?"
        args.append(bureau_code)
    if severity:
        where += " AND severity = ?"
        args.append(severity)
    total = db.execute(f"SELECT count(*) FROM data_anomalies{where}", args).fetchone()[0]
    sql = (
        f"SELECT * FROM data_anomalies{where} "
        "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, id DESC "
        "LIMIT ? OFFSET ?"
    )
    rows = db.execute(sql, [*args, limit, offset]).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.pop("evidence_json") or "{}")
        except json.JSONDecodeError:
            item["evidence"] = {}
        items.append(item)
    if owns:
        db.close()
    return {"total": int(total), "offset": offset, "limit": limit, "items": items}


def apply_correction(
    *,
    target_type: str,
    target_id: str,
    patch: dict[str, Any],
    note: str | None = None,
    anomaly_id: int | None = None,
    delete: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Apply a manual patch (or delete) and write an audit row."""
    owns = conn is None
    db = conn or connect()
    target_type = target_type.strip()
    target_id = str(target_id).strip()
    if target_type not in {"appointment_event", "leader_duty", "person"}:
        raise ValueError("target_type must be appointment_event|leader_duty|person")

    if delete:
        result = _delete_target(db, target_type, target_id, note=note, anomaly_id=anomaly_id)
    else:
        if not patch:
            raise ValueError("patch is required unless delete=true")
        result = _patch_target(db, target_type, target_id, patch, note=note, anomaly_id=anomaly_id)

    if anomaly_id is not None:
        _resolve_anomaly(db, anomaly_id)
    elif result.get("person_name") or result.get("old"):
        # Resolve open anomalies pointing at this target.
        db.execute(
            """
            UPDATE data_anomalies
            SET status='resolved', resolved_at=?
            WHERE target_type=? AND target_id=? AND status='open'
            """,
            (_now(), target_type, target_id),
        )

    db.commit()
    if owns:
        db.close()
    return result


def ignore_anomaly(anomaly_id: int, *, note: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    row = db.execute("SELECT * FROM data_anomalies WHERE id = ?", (anomaly_id,)).fetchone()
    if row is None:
        raise KeyError(f"anomaly {anomaly_id} not found")
    db.execute(
        "UPDATE data_anomalies SET status='ignored', resolved_at=? WHERE id=?",
        (_now(), anomaly_id),
    )
    db.execute(
        """
        INSERT INTO manual_corrections (
            target_type, target_id, field_name, old_value, new_value, patch_json, note, anomaly_id, created_at
        ) VALUES (?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
        """,
        (
            row["target_type"],
            row["target_id"],
            json.dumps({"action": "ignore"}, ensure_ascii=False),
            note or f"ignore anomaly {row['kind']}",
            anomaly_id,
            _now(),
        ),
    )
    db.commit()
    out = {"id": anomaly_id, "status": "ignored"}
    if owns:
        db.close()
    return out


def list_corrections(
    *,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    total = db.execute("SELECT count(*) FROM manual_corrections").fetchone()[0]
    rows = db.execute(
        """
        SELECT * FROM manual_corrections
        ORDER BY id DESC LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    items = [dict(r) for r in rows]
    if owns:
        db.close()
    return {"total": int(total), "items": items}


def _scan_events(db: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    today = date.today()
    for row in db.execute("SELECT * FROM appointment_events"):
        eid = str(row["id"])
        name = row["person_name"]
        bureau = row["bureau_code"]
        if not is_plausible_person_name(name):
            out.append(
                _anom(
                    "implausible_name",
                    "error",
                    "appointment_event",
                    eid,
                    bureau,
                    name,
                    f"任免人名可疑: {name!r}",
                    {"person_name": name, "source_url": row["source_url"]},
                )
            )
        if not (row["title_raw"] or "").strip() and not (row["department_raw"] or "").strip():
            out.append(
                _anom(
                    "missing_fields",
                    "warn",
                    "appointment_event",
                    eid,
                    bureau,
                    name,
                    "任免记录缺少职务与科室",
                    {"source_url": row["source_url"]},
                )
            )
        if not (row["effective_on"] or "").strip():
            out.append(
                _anom(
                    "missing_date",
                    "warn",
                    "appointment_event",
                    eid,
                    bureau,
                    name,
                    "任免缺少生效日期",
                    {"notice_title": row["notice_title"]},
                )
            )
        else:
            day = _parse_day(row["effective_on"])
            if day is None:
                out.append(
                    _anom(
                        "bad_date",
                        "warn",
                        "appointment_event",
                        eid,
                        bureau,
                        name,
                        f"日期格式异常: {row['effective_on']!r}",
                        {"effective_on": row["effective_on"]},
                    )
                )
            else:
                if day > today:
                    # allow small future slack (官网常超前公示）；> today+365 才标
                    if (day - today).days > 365:
                        out.append(
                            _anom(
                                "date_anomaly",
                                "warn",
                                "appointment_event",
                                eid,
                                bureau,
                                name,
                                f"生效日过远的未来: {day.isoformat()}",
                                {"effective_on": row["effective_on"]},
                            )
                        )
                elif day.year < 1994:
                    out.append(
                        _anom(
                            "date_anomaly",
                            "warn",
                            "appointment_event",
                            eid,
                            bureau,
                            name,
                            f"生效日过早: {day.isoformat()}",
                            {"effective_on": row["effective_on"]},
                        )
                    )
        dept = row["department_raw"] or ""
        if len(dept) > 40 or _NOISE_DEPT_RE.search(dept):
            out.append(
                _anom(
                    "parse_noise",
                    "warn",
                    "appointment_event",
                    eid,
                    bureau,
                    name,
                    "科室字段疑似解析噪声",
                    {"department_raw": dept[:120]},
                )
            )
    return out


def _scan_leaders(db: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in db.execute("SELECT * FROM leader_duties"):
        lid = str(row["id"])
        name = row["person_name"]
        bureau = row["bureau_code"]
        if not is_plausible_person_name(name):
            out.append(
                _anom(
                    "implausible_name",
                    "error",
                    "leader_duty",
                    lid,
                    bureau,
                    name,
                    f"领导人名可疑: {name!r}",
                    {"source_url": row["source_url"]},
                )
            )
        try:
            deps = json.loads(row["departments_json"] or "[]")
        except json.JSONDecodeError:
            deps = []
            out.append(
                _anom(
                    "parse_noise",
                    "warn",
                    "leader_duty",
                    lid,
                    bureau,
                    name,
                    "departments_json 无法解析",
                    {"departments_json": row["departments_json"]},
                )
            )
        if not deps and (row["duty_summary"] or "") in {"分管工作", "分管"}:
            out.append(
                _anom(
                    "missing_fields",
                    "info",
                    "leader_duty",
                    lid,
                    bureau,
                    name,
                    "领导标注分管但无科室列表",
                    {"title_raw": row["title_raw"]},
                )
            )
    return out


def _scan_same_name(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Same plausible name appearing in many bureaus — possible homonym."""
    counts: dict[str, set[str]] = defaultdict(set)
    for row in db.execute("SELECT name, bureau_code FROM persons"):
        if is_plausible_person_name(row["name"]):
            counts[row["name"]].add(row["bureau_code"])
    out: list[dict[str, Any]] = []
    for name, bureaus in counts.items():
        if len(bureaus) < 3:
            continue
        out.append(
            _anom(
                "same_name_multi_bureau",
                "info",
                "person_name",
                name,
                None,
                name,
                f"同名出现在 {len(bureaus)} 个单位，可能需人工区分",
                {"bureaus": sorted(bureaus)},
            )
        )
    return out


def _scan_conflicts(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Same person/bureau: appoint and dismiss same title+dept on same day."""
    out: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in db.execute(
        """
        SELECT * FROM appointment_events
        WHERE effective_on IS NOT NULL AND effective_on != ''
        """
    ):
        day = (row["effective_on"] or "")[:10]
        key = (row["bureau_code"], row["person_name"], day)
        grouped[key].append(row)
    for (bureau, name, day), rows in grouped.items():
        actions = {r["action"] for r in rows}
        if "appoint" in actions and "dismiss" in actions:
            # only flag when overlapping title/dept text
            titles = {(r["title_raw"] or "", r["department_raw"] or "") for r in rows}
            if len(titles) <= 2:
                ids = [str(r["id"]) for r in rows]
                out.append(
                    _anom(
                        "conflict",
                        "warn",
                        "appointment_event",
                        ids[0],
                        bureau,
                        name,
                        f"{day} 同日既有任命又有免职，请核对",
                        {"event_ids": ids, "day": day},
                    )
                )
    return out


def _anom(
    kind: str,
    severity: str,
    target_type: str,
    target_id: str,
    bureau_code: str | None,
    person_name: str | None,
    message: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "target_type": target_type,
        "target_id": str(target_id),
        "bureau_code": bureau_code,
        "person_name": person_name,
        "message": message,
        "evidence": evidence,
    }


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    if not _DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _patch_target(
    db: sqlite3.Connection,
    target_type: str,
    target_id: str,
    patch: dict[str, Any],
    *,
    note: str | None,
    anomaly_id: int | None,
) -> dict[str, Any]:
    if target_type == "appointment_event":
        allowed = EVENT_PATCH_FIELDS
        table = "appointment_events"
        id_col = "id"
    elif target_type == "leader_duty":
        allowed = LEADER_PATCH_FIELDS
        table = "leader_duties"
        id_col = "id"
    else:
        allowed = PERSON_PATCH_FIELDS
        table = "persons"
        id_col = "id"

    bad = set(patch) - allowed
    if bad:
        raise ValueError(f"unsupported fields for {target_type}: {sorted(bad)}")

    row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (target_id,)).fetchone()
    if row is None:
        raise KeyError(f"{target_type} {target_id} not found")

    old_snapshot = {k: row[k] for k in patch}
    sets = ", ".join(f"{k}=?" for k in patch)
    values = list(patch.values()) + [target_id]
    db.execute(f"UPDATE {table} SET {sets} WHERE {id_col} = ?", values)

    # Keep sibling events / leaders / persons in sync when renaming.
    if "person_name" in patch and target_type in {"appointment_event", "leader_duty"}:
        old_name = row["person_name"]
        new_name = patch["person_name"]
        bureau = row["bureau_code"]
        if is_plausible_person_name(new_name) and old_name != new_name:
            db.execute(
                """
                UPDATE appointment_events
                SET person_name = ?
                WHERE bureau_code = ? AND person_name = ?
                """,
                (new_name, bureau, old_name),
            )
            db.execute(
                """
                UPDATE leader_duties
                SET person_name = ?
                WHERE bureau_code = ? AND person_name = ?
                """,
                (new_name, bureau, old_name),
            )
            db.execute(
                """
                UPDATE data_anomalies
                SET person_name = ?
                WHERE bureau_code = ? AND person_name = ? AND status = 'open'
                """,
                (new_name, bureau, old_name),
            )
            new_id = f"{bureau}:{new_name}"
            old_id = f"{bureau}:{old_name}"
            existing = db.execute("SELECT id FROM persons WHERE id = ?", (new_id,)).fetchone()
            if existing:
                db.execute("DELETE FROM persons WHERE id = ?", (old_id,))
            else:
                db.execute(
                    "UPDATE persons SET id=?, name=? WHERE id=?",
                    (new_id, new_name, old_id),
                )
    elif "name" in patch and target_type == "person":
        old_name = row["name"]
        new_name = patch["name"]
        bureau = row["bureau_code"]
        if is_plausible_person_name(new_name) and old_name != new_name:
            db.execute(
                """
                UPDATE appointment_events
                SET person_name = ?
                WHERE bureau_code = ? AND person_name = ?
                """,
                (new_name, bureau, old_name),
            )
            db.execute(
                """
                UPDATE leader_duties
                SET person_name = ?
                WHERE bureau_code = ? AND person_name = ?
                """,
                (new_name, bureau, old_name),
            )
            new_id = f"{bureau}:{new_name}"
            old_id = row["id"]
            existing = db.execute("SELECT id FROM persons WHERE id = ?", (new_id,)).fetchone()
            if existing and existing["id"] != old_id:
                db.execute("DELETE FROM persons WHERE id = ?", (old_id,))
            else:
                db.execute(
                    "UPDATE persons SET id=?, name=? WHERE id=?",
                    (new_id, new_name, old_id),
                )

    for field, new_val in patch.items():
        db.execute(
            """
            INSERT INTO manual_corrections (
                target_type, target_id, field_name, old_value, new_value, patch_json, note, anomaly_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_type,
                target_id,
                field,
                None if old_snapshot.get(field) is None else str(old_snapshot.get(field)),
                None if new_val is None else str(new_val),
                json.dumps(patch, ensure_ascii=False),
                note,
                anomaly_id,
                _now(),
            ),
        )
    return {
        "target_type": target_type,
        "target_id": target_id,
        "old": old_snapshot,
        "new": patch,
        "note": note,
    }


def _delete_target(
    db: sqlite3.Connection,
    target_type: str,
    target_id: str,
    *,
    note: str | None,
    anomaly_id: int | None,
) -> dict[str, Any]:
    if target_type == "appointment_event":
        table, id_col = "appointment_events", "id"
    elif target_type == "leader_duty":
        table, id_col = "leader_duties", "id"
    else:
        table, id_col = "persons", "id"
    row = db.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (target_id,)).fetchone()
    if row is None:
        raise KeyError(f"{target_type} {target_id} not found")
    snapshot = dict(row)
    db.execute(f"DELETE FROM {table} WHERE {id_col} = ?", (target_id,))
    db.execute(
        """
        INSERT INTO manual_corrections (
            target_type, target_id, field_name, old_value, new_value, patch_json, note, anomaly_id, created_at
        ) VALUES (?, ?, '__deleted__', ?, NULL, ?, ?, ?, ?)
        """,
        (
            target_type,
            target_id,
            json.dumps(snapshot, ensure_ascii=False, default=str),
            json.dumps({"action": "delete"}, ensure_ascii=False),
            note or "delete",
            anomaly_id,
            _now(),
        ),
    )
    return {"target_type": target_type, "target_id": target_id, "deleted": True, "old": snapshot}


def _resolve_anomaly(db: sqlite3.Connection, anomaly_id: int) -> None:
    db.execute(
        "UPDATE data_anomalies SET status='resolved', resolved_at=? WHERE id=?",
        (_now(), anomaly_id),
    )
