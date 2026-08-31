"""Email OTP login + cookie sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from tax_platform.accounts.schema import connect

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_TTL_MINUTES = 15
SESSION_DAYS = 30
COOKIE_NAME = "tax_hr_session"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def secret_key() -> str:
    return os.environ.get("TAX_HR_SECRET", "dev-insecure-tax-hr-secret")


def is_dev_mode() -> bool:
    return os.environ.get("TAX_HR_DEV_LOGIN", "1").strip() not in {"0", "false", "False"}


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if not EMAIL_RE.match(value):
        raise ValueError("邮箱格式不正确")
    return value


def _hash_code(email: str, code: str) -> str:
    raw = f"{email}:{code}:{secret_key()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(f"{token}:{secret_key()}".encode("utf-8")).hexdigest()


def request_login_code(
    email: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Create a 6-digit OTP for ``email``. Dev mode returns the plaintext code."""
    owns = conn is None
    db = conn or connect()
    email = normalize_email(email)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    expires = now + timedelta(minutes=CODE_TTL_MINUTES)
    db.execute(
        """
        INSERT INTO login_codes (email, code_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (email, _hash_code(email, code), _iso(expires), _iso(now)),
    )
    db.commit()
    out: dict[str, Any] = {
        "ok": True,
        "email": email,
        "expires_in_seconds": CODE_TTL_MINUTES * 60,
        "dev_mode": is_dev_mode(),
    }
    if is_dev_mode():
        out["dev_code"] = code
    if owns:
        db.close()
    return out


def verify_login_code(
    email: str,
    code: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    email = normalize_email(email)
    code = (code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("验证码应为 6 位数字")

    now = _now()
    row = db.execute(
        """
        SELECT id, code_hash, expires_at, consumed_at
        FROM login_codes
        WHERE email = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (email,),
    ).fetchone()
    if row is None:
        raise ValueError("请先获取验证码")
    if row["consumed_at"]:
        raise ValueError("验证码已使用，请重新获取")
    if row["expires_at"] < _iso(now):
        raise ValueError("验证码已过期，请重新获取")
    if not hmac.compare_digest(row["code_hash"], _hash_code(email, code)):
        raise ValueError("验证码不正确")

    db.execute(
        "UPDATE login_codes SET consumed_at = ? WHERE id = ?",
        (_iso(now), row["id"]),
    )
    user = db.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
    if user is None:
        cur = db.execute(
            "INSERT INTO users (email, created_at, last_login_at) VALUES (?, ?, ?)",
            (email, _iso(now), _iso(now)),
        )
        user_id = int(cur.lastrowid)
    else:
        user_id = int(user["id"])
        db.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (_iso(now), user_id),
        )

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires = now + timedelta(days=SESSION_DAYS)
    db.execute(
        """
        INSERT INTO sessions (token, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token_hash, user_id, _iso(now), _iso(expires)),
    )
    db.commit()
    out = {
        "ok": True,
        "token": raw_token,
        "user": {"id": user_id, "email": email},
        "expires_at": _iso(expires),
    }
    if owns:
        db.close()
    return out


def session_user(
    token: str | None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    if not token:
        return None
    owns = conn is None
    db = conn or connect()
    token_hash = _hash_token(token.strip())
    row = db.execute(
        """
        SELECT s.token, s.expires_at, u.id AS user_id, u.email
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
        """,
        (token_hash,),
    ).fetchone()
    if row is None:
        if owns:
            db.close()
        return None
    if row["expires_at"] < _iso(_now()):
        db.execute("DELETE FROM sessions WHERE token = ?", (token_hash,))
        db.commit()
        if owns:
            db.close()
        return None
    out = {"id": int(row["user_id"]), "email": row["email"]}
    if owns:
        db.close()
    return out


def logout(token: str | None, *, conn: sqlite3.Connection | None = None) -> None:
    if not token:
        return
    owns = conn is None
    db = conn or connect()
    db.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token.strip()),))
    db.commit()
    if owns:
        db.close()
