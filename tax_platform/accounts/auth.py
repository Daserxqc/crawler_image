"""Email / phone OTP login + cookie sessions."""

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

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# Mainland mobile: 1[3-9] + 9 digits; also allow +86 / 86 prefix.
PHONE_RE = re.compile(r"^(?:\+?86)?(1[3-9]\d{9})$")
CODE_TTL_MINUTES = 15
SESSION_DAYS = 30
COOKIE_NAME = "tax_hr_session"
PHONE_PREFIX = "phone:"
NICKNAME_RE = re.compile(r"^[\w\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff·\-]{2,16}$")


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


def normalize_phone(phone: str) -> str:
    raw = (phone or "").strip().replace(" ", "").replace("-", "")
    match = PHONE_RE.fullmatch(raw)
    if not match:
        raise ValueError("手机号格式不正确，请输入 11 位大陆手机号")
    return match.group(1)


def normalize_account(channel: str, account: str) -> tuple[str, str]:
    """Return (channel, identity_key stored in login_codes / users.email)."""
    channel = (channel or "email").strip().lower()
    account = (account or "").strip()
    # Auto-correct common client mistakes (phone typed while channel=email).
    if channel == "email" and "@" not in account:
        cleaned = account.replace(" ", "").replace("-", "")
        if PHONE_RE.fullmatch(cleaned) or PHONE_RE.fullmatch(
            cleaned[2:] if cleaned.startswith("86") else cleaned
        ):
            channel = "phone"
    if channel == "phone" and "@" in account:
        channel = "email"
    if channel == "email":
        return "email", normalize_email(account)
    if channel in {"phone", "mobile", "sms"}:
        return "phone", f"{PHONE_PREFIX}{normalize_phone(account)}"
    raise ValueError("登录方式须为 email 或 phone")


def resolve_login_payload(payload: dict[str, Any]) -> tuple[str, str]:
    """Pick channel + account from a request body, with phone/email autofix."""
    account = str(
        payload.get("account")
        or payload.get("phone")
        or payload.get("email")
        or ""
    ).strip()
    channel = str(payload.get("channel") or "").strip().lower()
    if not channel:
        if payload.get("phone") and not payload.get("email"):
            channel = "phone"
        elif "@" in account:
            channel = "email"
        else:
            cleaned = account.replace(" ", "").replace("-", "")
            channel = "phone" if PHONE_RE.fullmatch(cleaned) else "email"
    return normalize_account(channel, account)


def display_account(identity: str) -> str:
    if identity.startswith(PHONE_PREFIX):
        return identity[len(PHONE_PREFIX) :]
    return identity


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11:
        return f"{digits[:3]}****{digits[-4:]}"
    if len(digits) >= 7:
        return f"{digits[:2]}****{digits[-2:]}"
    return "***"


def mask_email(email: str) -> str:
    value = (email or "").strip()
    if "@" not in value:
        return "***"
    local, _, domain = value.partition("@")
    if not local:
        return f"*@{domain}"
    if len(local) == 1:
        return f"{local}***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_account(channel: str, account: str) -> str:
    if channel == "phone":
        return mask_phone(account)
    return mask_email(account)


def normalize_nickname(value: str) -> str:
    nick = (value or "").strip()
    if not nick:
        return ""
    if not NICKNAME_RE.fullmatch(nick):
        raise ValueError("昵称须为 2–16 个字符，可使用中文、字母、数字")
    return nick


def default_nickname(user_id: int, channel: str, account: str) -> str:
    """Friendly fallback when the user has not set a custom nickname."""
    if channel == "phone":
        digits = re.sub(r"\D", "", account or "")
        suffix = digits[-4:] if len(digits) >= 4 else f"{user_id:04d}"
        return f"用户{suffix}"
    return f"税务用户{user_id:04d}"


def update_nickname(
    user_id: int,
    nickname: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    nick = normalize_nickname(nickname)
    db.execute(
        "UPDATE users SET nickname = ? WHERE id = ?",
        (nick or None, user_id),
    )
    db.commit()
    row = db.execute(
        "SELECT id, email, nickname, created_at, last_login_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        if owns:
            db.close()
        raise KeyError(f"user {user_id} not found")
    out = _user_payload(
        int(row["id"]),
        row["email"],
        nickname=row["nickname"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )
    if owns:
        db.close()
    return out


def _hash_code(identity: str, code: str) -> str:
    raw = f"{identity}:{code}:{secret_key()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(f"{token}:{secret_key()}".encode("utf-8")).hexdigest()


def _user_payload(
    user_id: int,
    identity: str,
    *,
    nickname: str | None = None,
    created_at: str | None = None,
    last_login_at: str | None = None,
    session_expires_at: str | None = None,
) -> dict[str, Any]:
    """Public-facing user object: never expose full phone/email to the client."""
    channel = "phone" if identity.startswith(PHONE_PREFIX) else "email"
    account = display_account(identity)
    masked = mask_account(channel, account)
    custom_nick = (nickname or "").strip()
    default_nick = default_nickname(user_id, channel, account)
    public_name = custom_nick or default_nick
    return {
        "id": user_id,
        "channel": channel,
        "nickname": custom_nick or None,
        "default_nickname": default_nick,
        "nickname_is_custom": bool(custom_nick),
        "display_name": public_name,
        "account_masked": masked,
        "email_masked": masked if channel == "email" else None,
        "phone_masked": masked if channel == "phone" else None,
        "login_method": "手机号" if channel == "phone" else "邮箱",
        "created_at": created_at,
        "last_login_at": last_login_at,
        "session_expires_at": session_expires_at,
    }


def request_login_code(
    account: str = "",
    *,
    channel: str = "email",
    email: str | None = None,
    payload: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Create a 6-digit OTP. Dev mode returns the plaintext code.

    Prefer ``payload={...}`` so channel/account are resolved together.
    ``email`` is accepted as an alias of ``account`` for backward compatibility.
    """
    owns = conn is None
    db = conn or connect()
    if payload is not None:
        channel, identity = resolve_login_payload(payload)
    else:
        if email and not account:
            account = email
            channel = "email"
        channel, identity = normalize_account(channel, account)
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    expires = now + timedelta(minutes=CODE_TTL_MINUTES)
    db.execute(
        """
        INSERT INTO login_codes (email, code_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (identity, _hash_code(identity, code), _iso(expires), _iso(now)),
    )
    db.commit()
    out: dict[str, Any] = {
        "ok": True,
        "channel": channel,
        "account": display_account(identity),
        "email": display_account(identity) if channel == "email" else None,
        "phone": display_account(identity) if channel == "phone" else None,
        "expires_in_seconds": CODE_TTL_MINUTES * 60,
        "dev_mode": is_dev_mode(),
    }
    if is_dev_mode():
        out["dev_code"] = code
    if owns:
        db.close()
    return out


def verify_login_code(
    account: str = "",
    code: str = "",
    *,
    channel: str = "email",
    email: str | None = None,
    payload: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    if payload is not None:
        channel, identity = resolve_login_payload(payload)
        code = str(payload.get("code") or code or "")
    else:
        if email and not account:
            account = email
            channel = "email"
        channel, identity = normalize_account(channel, account)
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
        (identity,),
    ).fetchone()
    if row is None:
        raise ValueError("请先获取验证码")
    if row["consumed_at"]:
        raise ValueError("验证码已使用，请重新获取")
    if row["expires_at"] < _iso(now):
        raise ValueError("验证码已过期，请重新获取")
    if not hmac.compare_digest(row["code_hash"], _hash_code(identity, code)):
        raise ValueError("验证码不正确")

    db.execute(
        "UPDATE login_codes SET consumed_at = ? WHERE id = ?",
        (_iso(now), row["id"]),
    )
    user = db.execute(
        "SELECT id, email, nickname FROM users WHERE email = ?", (identity,)
    ).fetchone()
    if user is None:
        cur = db.execute(
            "INSERT INTO users (email, created_at, last_login_at) VALUES (?, ?, ?)",
            (identity, _iso(now), _iso(now)),
        )
        user_id = int(cur.lastrowid)
        user_nick = None
    else:
        user_id = int(user["id"])
        user_nick = user["nickname"]
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
    fresh = db.execute(
        "SELECT id, email, nickname, created_at, last_login_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    out = {
        "ok": True,
        "token": raw_token,
        "user": _user_payload(
            user_id,
            fresh["email"],
            nickname=fresh["nickname"],
            created_at=fresh["created_at"],
            last_login_at=fresh["last_login_at"],
        ),
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
        SELECT s.token, s.expires_at, u.id AS user_id, u.email, u.nickname,
               u.created_at, u.last_login_at
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
    out = _user_payload(
        int(row["user_id"]),
        row["email"],
        nickname=row["nickname"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
        session_expires_at=row["expires_at"],
    )
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
