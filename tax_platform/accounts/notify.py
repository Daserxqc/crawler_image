"""Match watched targets to new appointment events and queue/send email."""

from __future__ import annotations

import json
import os
import smtplib
import sqlite3
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

from tax_platform.accounts.schema import connect
from tax_platform.config.sites import get_site
from tax_platform.normalize.change import classify_change

CHANGE_LABELS = {
    "appoint": "任职",
    "dismiss": "免职",
    "promote": "晋升",
    "transfer": "调任",
    "retire": "退休",
    "probation_confirm": "试用期满",
    "unknown": "其他",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _bureau_name(code: str) -> str:
    try:
        return get_site(code).name
    except KeyError:
        return code


def _event_matches_watch(event: sqlite3.Row, watch: sqlite3.Row) -> bool:
    if watch["target_type"] == "bureau":
        return event["bureau_code"] == watch["target_id"]
    if watch["target_type"] == "person":
        target = watch["target_id"]
        # person id is usually ``bureau:name``; also allow bare name.
        if ":" in target:
            bureau, name = target.split(":", 1)
            return event["person_name"] == name and (
                not bureau or event["bureau_code"] == bureau
            )
        return event["person_name"] == target
    return False


def _format_event_line(event: sqlite3.Row) -> str:
    ctype = classify_change(
        action=event["action"],
        title_raw=event["title_raw"],
        department_raw=event["department_raw"],
        notice_title=event["notice_title"],
        raw_clause=event["raw_clause"],
    )
    label = CHANGE_LABELS.get(ctype, ctype)
    bits = [
        event["effective_on"] or "日期未知",
        event["person_name"],
        label,
        event["title_raw"] or "",
        event["department_raw"] or "",
        _bureau_name(event["bureau_code"]),
    ]
    line = " · ".join(b for b in bits if b)
    if event["source_url"]:
        line += f"\n  原文: {event['source_url']}"
    if event["raw_clause"]:
        line += f"\n  条款: {event['raw_clause']}"
    return line


def collect_pending_for_user(
    user_id: int,
    *,
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    watches = conn.execute(
        "SELECT * FROM watches WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    if not watches:
        return []

    cursor = conn.execute(
        "SELECT last_event_id FROM notify_cursor WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    last_id = int(cursor["last_event_id"]) if cursor else 0

    events = conn.execute(
        """
        SELECT id, bureau_code, person_name, action, bureau_name, department_raw,
               title_raw, effective_on, notice_title, source_url, raw_clause
        FROM appointment_events
        WHERE id > ?
        ORDER BY id ASC
        """,
        (last_id,),
    ).fetchall()

    matched: list[dict[str, Any]] = []
    for event in events:
        hit_watches = [dict(w) for w in watches if _event_matches_watch(event, w)]
        if not hit_watches:
            continue
        matched.append(
            {
                "event": dict(event),
                "watches": hit_watches,
                "line": _format_event_line(event),
            }
        )
    return matched


def queue_digest(
    user_id: int,
    matches: list[dict[str, Any]],
    *,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    if not matches:
        return None
    user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    body_lines = [
        "您好，",
        "",
        f"自上次通知以来，您关注的单位/人员共有 {len(matches)} 条新变动：",
        "",
    ]
    for item in matches:
        labels = "、".join(
            w.get("label") or w["target_id"] for w in item["watches"]
        )
        body_lines.append(f"[{labels}]")
        body_lines.append(item["line"])
        body_lines.append("")
    body_lines.extend(
        [
            "—",
            "本邮件由税局人事检索关注推送自动生成。",
            "可在「我的关注」中管理订阅。",
        ]
    )
    body = "\n".join(body_lines)
    event_ids = [int(m["event"]["id"]) for m in matches]
    cur = conn.execute(
        """
        INSERT INTO email_outbox (
            user_id, to_email, subject, body_text, event_ids_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            user_id,
            user["email"],
            f"【税局人事】您关注的对象有 {len(matches)} 条新变动",
            body,
            json.dumps(event_ids, ensure_ascii=False),
            _iso_now(),
        ),
    )
    max_event_id = max(event_ids)
    conn.execute(
        """
        INSERT INTO notify_cursor (user_id, last_event_id, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            last_event_id = excluded.last_event_id,
            updated_at = excluded.updated_at
        """,
        (user_id, max_event_id, _iso_now()),
    )
    return {"outbox_id": int(cur.lastrowid), "event_count": len(matches)}


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_outbox_row(row: sqlite3.Row | dict[str, Any]) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    mail_from = os.environ.get("SMTP_FROM", "")
    use_tls = os.environ.get("SMTP_TLS", "1").strip() not in {"0", "false", "False"}

    msg = EmailMessage()
    msg["Subject"] = row["subject"]
    msg["From"] = mail_from
    msg["To"] = row["to_email"]
    msg.set_content(row["body_text"])

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)


def flush_outbox(
    *,
    dry_run: bool = False,
    limit: int = 50,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    rows = db.execute(
        """
        SELECT * FROM email_outbox
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    sent = 0
    failed = 0
    skipped = 0
    for row in rows:
        if dry_run or not smtp_configured():
            db.execute(
                """
                UPDATE email_outbox
                SET status = 'dry_run', sent_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    _iso_now(),
                    None if dry_run else "SMTP not configured; marked dry_run",
                    row["id"],
                ),
            )
            skipped += 1
            continue
        try:
            send_outbox_row(row)
            db.execute(
                """
                UPDATE email_outbox
                SET status = 'sent', sent_at = ?, error = NULL
                WHERE id = ?
                """,
                (_iso_now(), row["id"]),
            )
            sent += 1
        except Exception as exc:  # noqa: BLE001 — surface in outbox
            db.execute(
                """
                UPDATE email_outbox
                SET status = 'failed', error = ?
                WHERE id = ?
                """,
                (str(exc)[:500], row["id"]),
            )
            failed += 1
    db.commit()
    out = {
        "pending_seen": len(rows),
        "sent": sent,
        "failed": failed,
        "dry_run_or_skipped": skipped,
        "smtp_configured": smtp_configured(),
    }
    if owns:
        db.close()
    return out


def run_notify_cycle(
    *,
    dry_run: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    users = db.execute(
        """
        SELECT DISTINCT u.id, u.email
        FROM users u
        JOIN watches w ON w.user_id = u.id
        """
    ).fetchall()
    queued = 0
    event_total = 0
    for user in users:
        matches = collect_pending_for_user(int(user["id"]), conn=db)
        result = queue_digest(int(user["id"]), matches, conn=db)
        if result:
            queued += 1
            event_total += int(result["event_count"])
    db.commit()
    flush = flush_outbox(dry_run=dry_run, conn=db)
    out = {
        "users_with_watches": len(users),
        "digests_queued": queued,
        "events_matched": event_total,
        "flush": flush,
    }
    if owns:
        db.close()
    return out
