"""Invite-based username/password login + cookie sessions."""

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

SESSION_DAYS = 30
COOKIE_NAME = "tax_hr_session"
USER_PREFIX = "user:"
PHONE_PREFIX = "phone:"
NICKNAME_RE = re.compile(r"^[\w\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff·\-]{2,16}$")
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$")
PASSWORD_MIN = 6
PBKDF2_ROUNDS = 260_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def secret_key() -> str:
    return os.environ.get("TAX_HR_SECRET", "dev-insecure-tax-hr-secret")


def _hash_token(token: str) -> str:
    return hashlib.sha256(f"{token}:{secret_key()}".encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ROUNDS,
    )
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not password:
        return False
    try:
        algo, rounds_s, salt_hex, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        rounds = int(rounds_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(got, expected)


def normalize_username(value: str) -> str:
    name = (value or "").strip()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("用户名须为 3–32 位，字母开头，仅含字母数字下划线")
    return name.lower()


def normalize_nickname(value: str) -> str:
    nick = (value or "").strip()
    if not nick:
        return ""
    if not NICKNAME_RE.fullmatch(nick):
        raise ValueError("昵称须为 2–16 个字符，可使用中文、字母、数字")
    return nick


def validate_password(password: str) -> str:
    pwd = password or ""
    if len(pwd) < PASSWORD_MIN:
        raise ValueError(f"密码至少 {PASSWORD_MIN} 位")
    if len(pwd) > 128:
        raise ValueError("密码过长")
    return pwd


def default_nickname(user_id: int, username: str | None = None) -> str:
    if username:
        return username
    return f"用户{user_id:04d}"


def identity_for_username(username: str) -> str:
    return f"{USER_PREFIX}{username}"


def display_account(identity: str) -> str:
    """Legacy helper for notify outbox addressing."""
    if not identity:
        return ""
    if identity.startswith(USER_PREFIX):
        return identity[len(USER_PREFIX) :]
    if identity.startswith(PHONE_PREFIX):
        return identity[len(PHONE_PREFIX) :]
    return identity


def generate_invite_username(*, conn: sqlite3.Connection | None = None) -> str:
    """``taxuser`` + 8 hex chars, e.g. taxusera1b2c3d4."""
    owns = conn is None
    db = conn or connect()
    try:
        for _ in range(20):
            candidate = f"taxuser{secrets.token_hex(4)}"
            exists = db.execute(
                "SELECT 1 FROM users WHERE username = ?", (candidate,)
            ).fetchone()
            if not exists:
                return candidate
        raise ValueError("无法生成唯一用户名，请重试")
    finally:
        if owns:
            db.close()


def generate_default_password() -> str:
    """12 chars: 6 letters + 6 digits, shuffled (easy to read aloud)."""
    letters = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(6))
    digits = "".join(secrets.choice("23456789") for _ in range(6))
    chars = list(letters + digits)
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


DEFAULT_INVITE_NICKNAME = "税务用户"


def allocate_invite_nickname(conn: sqlite3.Connection) -> str:
    """Distinct nicknames like 税务用户1234."""
    for _ in range(80):
        nick = f"{DEFAULT_INVITE_NICKNAME}{secrets.randbelow(9000) + 1000}"
        exists = conn.execute(
            "SELECT 1 FROM users WHERE nickname = ?", (nick,)
        ).fetchone()
        if not exists:
            return nick
    return f"{DEFAULT_INVITE_NICKNAME}{secrets.token_hex(2)}"


def generate_invite_token() -> str:
    return secrets.token_urlsafe(24)


def _row_username(row: Any) -> str | None:
    if row is None:
        return None
    try:
        return (row["username"] or "").strip() or None
    except (KeyError, IndexError, TypeError):
        return None


def _user_payload(
    user_id: int,
    *,
    username: str | None = None,
    nickname: str | None = None,
    is_admin: int | bool = 0,
    must_change_password: int | bool = 0,
    created_at: str | None = None,
    last_login_at: str | None = None,
    session_expires_at: str | None = None,
    identity: str | None = None,
) -> dict[str, Any]:
    uname = (username or "").strip()
    if not uname and identity and identity.startswith(USER_PREFIX):
        uname = identity[len(USER_PREFIX) :]
    custom_nick = (nickname or "").strip()
    default_nick = default_nickname(user_id, uname or None)
    public_name = custom_nick or default_nick
    return {
        "id": user_id,
        "username": uname or None,
        "channel": "password",
        "nickname": custom_nick or None,
        "default_nickname": default_nick,
        "nickname_is_custom": bool(custom_nick),
        "display_name": public_name,
        "account_masked": uname or f"user#{user_id}",
        "email_masked": None,
        "phone_masked": None,
        "login_method": "账号密码",
        "is_admin": bool(is_admin),
        "must_change_password": bool(must_change_password),
        "created_at": created_at,
        "last_login_at": last_login_at,
        "session_expires_at": session_expires_at,
    }


def _payload_from_row(row: Any, *, session_expires_at: str | None = None) -> dict[str, Any]:
    return _user_payload(
        int(row["id"] if "id" in row.keys() else row["user_id"]),
        username=_row_username(row),
        nickname=row["nickname"],
        is_admin=int(row["is_admin"] or 0) if "is_admin" in row.keys() else 0,
        must_change_password=int(row["must_change_password"] or 0)
        if "must_change_password" in row.keys()
        else 0,
        created_at=row["created_at"] if "created_at" in row.keys() else None,
        last_login_at=row["last_login_at"] if "last_login_at" in row.keys() else None,
        session_expires_at=session_expires_at,
        identity=row["email"] if "email" in row.keys() else None,
    )


def ensure_bootstrap_admin(conn: sqlite3.Connection) -> None:
    """Create the first admin if none exists (env or defaults).

    Bootstrap admin is ready to use immediately — no forced password change.
    """
    username = normalize_username(os.environ.get("TAX_HR_ADMIN_USER", "admin"))
    # Clear accidental "must change" on the bootstrap admin (older builds set it).
    conn.execute(
        """
        UPDATE users
        SET must_change_password = 0
        WHERE username = ? AND COALESCE(is_admin, 0) = 1
        """,
        (username,),
    )
    row = conn.execute(
        "SELECT id FROM users WHERE COALESCE(is_admin, 0) = 1 LIMIT 1"
    ).fetchone()
    if row:
        conn.commit()
        return
    password = os.environ.get("TAX_HR_ADMIN_PASSWORD", "TaxHR-Admin-ChangeMe")
    now = _iso(_now())
    identity = identity_for_username(username)
    existing = conn.execute(
        "SELECT id FROM users WHERE username = ? OR email = ?",
        (username, identity),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE users SET
                username = ?,
                password_hash = ?,
                is_admin = 1,
                must_change_password = 0
            WHERE id = ?
            """,
            (username, hash_password(password), int(existing["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO users (
                email, username, password_hash, nickname, is_admin,
                must_change_password, created_at, last_login_at
            ) VALUES (?, ?, ?, ?, 1, 0, ?, NULL)
            """,
            (identity, username, hash_password(password), "管理员", now),
        )
    conn.commit()


def _create_session(db: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    now = _now()
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
    db.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?",
        (_iso(now), user_id),
    )
    return {"token": raw_token, "expires_at": _iso(expires)}


def login_with_password(
    username: str,
    password: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        uname = normalize_username(username)
        row = db.execute(
            """
            SELECT id, email, username, password_hash, nickname, is_admin,
                   must_change_password, created_at, last_login_at
            FROM users WHERE username = ?
            """,
            (uname,),
        ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise ValueError("用户名或密码不正确")
        session = _create_session(db, int(row["id"]))
        db.commit()
        fresh = db.execute(
            """
            SELECT id, email, username, nickname, is_admin, must_change_password,
                   created_at, last_login_at
            FROM users WHERE id = ?
            """,
            (int(row["id"]),),
        ).fetchone()
        return {
            "ok": True,
            "token": session["token"],
            "expires_at": session["expires_at"],
            "user": _payload_from_row(fresh, session_expires_at=session["expires_at"]),
        }
    finally:
        if owns:
            db.close()


def change_password(
    user_id: int,
    new_password: str,
    *,
    old_password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        row = db.execute(
            """
            SELECT id, username, password_hash, must_change_password, nickname,
                   is_admin, created_at, last_login_at, email
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"user {user_id} not found")
        must = bool(row["must_change_password"])
        if not must:
            if not old_password or not verify_password(old_password, row["password_hash"]):
                raise ValueError("当前密码不正确")
        elif old_password and not verify_password(old_password, row["password_hash"]):
            raise ValueError("当前密码不正确")
        pwd = validate_password(new_password)
        if old_password and hmac.compare_digest(old_password, pwd):
            raise ValueError("新密码不能与当前密码相同")
        db.execute(
            """
            UPDATE users SET
                password_hash = ?,
                must_change_password = 0,
                invite_password = NULL,
                invite_token = NULL
            WHERE id = ?
            """,
            (hash_password(pwd), user_id),
        )
        db.commit()
        fresh = db.execute(
            """
            SELECT id, email, username, nickname, is_admin, must_change_password,
                   created_at, last_login_at
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        return {"ok": True, "user": _payload_from_row(fresh)}
    finally:
        if owns:
            db.close()


def update_nickname(
    user_id: int,
    nickname: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        nick = normalize_nickname(nickname)
        db.execute(
            "UPDATE users SET nickname = ? WHERE id = ?",
            (nick or None, user_id),
        )
        db.commit()
        row = db.execute(
            """
            SELECT id, email, username, nickname, is_admin, must_change_password,
                   created_at, last_login_at
            FROM users WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"user {user_id} not found")
        return _payload_from_row(row)
    finally:
        if owns:
            db.close()


def session_user(
    token: str | None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    if not token:
        return None
    owns = conn is None
    db = conn or connect()
    try:
        token_hash = _hash_token(token.strip())
        row = db.execute(
            """
            SELECT s.token, s.expires_at,
                   u.id AS id, u.email, u.username, u.nickname,
                   u.is_admin, u.must_change_password,
                   u.created_at, u.last_login_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] < _iso(_now()):
            db.execute("DELETE FROM sessions WHERE token = ?", (token_hash,))
            db.commit()
            return None
        return _payload_from_row(row, session_expires_at=row["expires_at"])
    finally:
        if owns:
            db.close()


def logout(token: str | None, *, conn: sqlite3.Connection | None = None) -> None:
    if not token:
        return
    owns = conn is None
    db = conn or connect()
    try:
        db.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token.strip()),))
        db.commit()
    finally:
        if owns:
            db.close()


def get_invite(token: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        tok = (token or "").strip()
        if not tok:
            raise ValueError("邀请链接无效")
        row = db.execute(
            """
            SELECT id, username, nickname, invite_password, invite_created_at, must_change_password
            FROM users WHERE invite_token = ?
            """,
            (tok,),
        ).fetchone()
        if row is None:
            raise ValueError("邀请链接无效或已失效")
        return {
            "ok": True,
            "username": row["username"],
            "nickname": row["nickname"],
            "default_password": row["invite_password"],
            "must_change_password": bool(row["must_change_password"]),
            "invite_created_at": row["invite_created_at"],
        }
    finally:
        if owns:
            db.close()


def create_invited_user(
    *,
    username: str | None = None,
    nickname: str | None = None,
    default_password: str | None = None,
    is_admin: bool = False,
    auto: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        raw_name = (username or "").strip()
        use_auto = bool(auto) or not raw_name
        if use_auto:
            uname = generate_invite_username(conn=db)
            nick = normalize_nickname(nickname or "") or allocate_invite_nickname(db)
            pwd = validate_password(default_password or generate_default_password())
        else:
            uname = normalize_username(raw_name)
            nick = normalize_nickname(nickname or "") or uname
            pwd = validate_password(default_password or generate_default_password())
        token = generate_invite_token()
        now = _iso(_now())
        identity = identity_for_username(uname)
        exists = db.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (uname, identity),
        ).fetchone()
        if exists:
            raise ValueError("用户名已存在")
        cur = db.execute(
            """
            INSERT INTO users (
                email, username, password_hash, nickname, is_admin,
                must_change_password, invite_token, invite_password,
                invite_created_at, created_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
            """,
            (
                identity,
                uname,
                hash_password(pwd),
                nick,
                1 if is_admin else 0,
                token,
                pwd,
                now,
                now,
            ),
        )
        user_id = int(cur.lastrowid)
        db.commit()
        return {
            "ok": True,
            "user_id": user_id,
            "username": uname,
            "nickname": nick,
            "default_password": pwd,
            "invite_token": token,
            "invite_path": f"/invite?token={token}",
            "must_change_password": True,
            "is_admin": bool(is_admin),
            "auto": use_auto,
        }
    finally:
        if owns:
            db.close()


def list_users(*, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        rows = db.execute(
            """
            SELECT u.id, u.username, u.nickname, u.is_admin, u.must_change_password,
                   u.invite_token, u.invite_created_at, u.created_at, u.last_login_at,
                   (SELECT COUNT(*) FROM watches w WHERE w.user_id = u.id) AS watch_count
            FROM users u
            ORDER BY u.is_admin DESC, u.id ASC
            """
        ).fetchall()
        items = []
        for r in rows:
            items.append(
                {
                    "id": int(r["id"]),
                    "username": r["username"],
                    "nickname": r["nickname"],
                    "display_name": (r["nickname"] or r["username"] or f"用户{r['id']}"),
                    "is_admin": bool(r["is_admin"]),
                    "must_change_password": bool(r["must_change_password"]),
                    "has_invite": bool(r["invite_token"]),
                    "invite_token": r["invite_token"],
                    "invite_path": f"/invite?token={r['invite_token']}"
                    if r["invite_token"]
                    else None,
                    "invite_created_at": r["invite_created_at"],
                    "created_at": r["created_at"],
                    "last_login_at": r["last_login_at"],
                    "watch_count": int(r["watch_count"] or 0),
                }
            )
        return {"total": len(items), "items": items}
    finally:
        if owns:
            db.close()


def reset_user_invite(
    user_id: int,
    *,
    default_password: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        row = db.execute(
            "SELECT id, username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise KeyError("用户不存在")
        pwd = validate_password(default_password or generate_default_password())
        token = generate_invite_token()
        now = _iso(_now())
        db.execute(
            """
            UPDATE users SET
                password_hash = ?,
                must_change_password = 1,
                invite_token = ?,
                invite_password = ?,
                invite_created_at = ?
            WHERE id = ?
            """,
            (hash_password(pwd), token, pwd, now, user_id),
        )
        # Drop existing sessions so old password cannot be used.
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.commit()
        return {
            "ok": True,
            "user_id": user_id,
            "username": row["username"],
            "default_password": pwd,
            "invite_token": token,
            "invite_path": f"/invite?token={token}",
        }
    finally:
        if owns:
            db.close()


def delete_user(
    user_id: int,
    *,
    actor_id: int,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = conn is None
    db = conn or connect()
    try:
        if int(user_id) == int(actor_id):
            raise ValueError("不能删除当前登录账号")
        row = db.execute("SELECT id, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError("用户不存在")
        if int(row["is_admin"] or 0):
            admins = db.execute(
                "SELECT COUNT(*) FROM users WHERE COALESCE(is_admin, 0) = 1"
            ).fetchone()[0]
            if int(admins) <= 1:
                raise ValueError("不能删除唯一的管理员")
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM watches WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM notify_cursor WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM email_outbox WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return {"ok": True}
    finally:
        if owns:
            db.close()


# --- Legacy stubs (OTP removed) ---

def request_login_code(*_a, **_k):  # noqa: ANN001
    raise ValueError("已改为账号密码登录，请使用邀请链接或管理员发放的账号")


def verify_login_code(*_a, **_k):  # noqa: ANN001
    raise ValueError("已改为账号密码登录，请使用邀请链接或管理员发放的账号")
