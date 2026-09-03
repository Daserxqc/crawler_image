"""Account-plane tables (same SQLite file, separate ownership)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tax_platform.store.schema import DEFAULT_DB_PATH, connect as store_connect

ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS login_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, target_type, target_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS email_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL,
    event_ids_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    error TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS notify_cursor (
    user_id INTEGER PRIMARY KEY,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_watches_user ON watches(user_id);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON email_outbox(status, created_at);
CREATE INDEX IF NOT EXISTS idx_login_codes_email ON login_codes(email, expires_at);
"""

_USER_COLUMNS = (
    ("username", "TEXT"),
    ("password_hash", "TEXT"),
    ("nickname", "TEXT"),
    ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
    ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("invite_token", "TEXT"),
    ("invite_password", "TEXT"),
    ("invite_created_at", "TEXT"),
)


def ensure_accounts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ACCOUNTS_SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for name, decl in _USER_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username "
        "ON users(username) WHERE username IS NOT NULL AND username != ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_invite_token "
        "ON users(invite_token) WHERE invite_token IS NOT NULL AND invite_token != ''"
    )
    conn.commit()


def connect(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    light: bool = False,
) -> sqlite3.Connection:
    """Open store DB and ensure account tables exist.

    ``light=True`` skips heavy store migrations/sync (for login/session paths)
    so auth stays responsive while crawlers hold other connections.
    """
    if light:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
    else:
        conn = store_connect(db_path)
    ensure_accounts_schema(conn)
    from tax_platform.accounts.auth import ensure_bootstrap_admin

    ensure_bootstrap_admin(conn)
    return conn
