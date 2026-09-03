"""Per-bureau list heads (latest 1–2) + weekly new-notice buffer.

Stored in a **separate** SQLite file (``output/list_heads.db``) so long list
scans do not lock the main ``tax_hr.db`` (accounts / notices / events).

``appointment_list_heads``
    Baseline for next compare (1–2 rows per bureau).

``appointment_list_updates``
    All newly discovered notices this run/week. Kept after ingest
    (``ingested_at`` set). Website feeds still come from main DB
    ``notices`` / ``appointment_events`` after batch ingest.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tax_platform.store.schema import DEFAULT_DB_PATH

DEFAULT_LIST_HEADS_DB_PATH = Path(DEFAULT_DB_PATH).resolve().parent / "list_heads.db"

LIST_HEADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS appointment_list_heads (
    bureau_code TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    published_on TEXT,
    has_update INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (bureau_code, rank)
);

CREATE INDEX IF NOT EXISTS idx_list_heads_update
    ON appointment_list_heads(has_update);
CREATE INDEX IF NOT EXISTS idx_list_heads_bureau
    ON appointment_list_heads(bureau_code);

CREATE TABLE IF NOT EXISTS appointment_list_updates (
    bureau_code TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    published_on TEXT,
    discovered_at TEXT NOT NULL,
    ingested_at TEXT,
    PRIMARY KEY (bureau_code, source_url)
);

CREATE INDEX IF NOT EXISTS idx_list_updates_bureau
    ON appointment_list_updates(bureau_code);
CREATE INDEX IF NOT EXISTS idx_list_updates_pending
    ON appointment_list_updates(ingested_at);

CREATE TABLE IF NOT EXISTS appointment_list_scan_failures (
    bureau_code TEXT PRIMARY KEY,
    list_url TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    failed_at TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT
);
"""


def ensure_list_heads_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LIST_HEADS_SCHEMA)
    _migrate_pending_to_updates(conn)
    _ensure_updates_columns(conn)


def _migrate_pending_to_updates(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "appointment_list_pending" not in tables:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO appointment_list_updates(
            bureau_code, source_url, title, published_on, discovered_at, ingested_at
        )
        SELECT bureau_code, source_url, title, published_on, created_at, NULL
        FROM appointment_list_pending
        """
    )
    conn.execute("DROP TABLE appointment_list_pending")


def _ensure_updates_columns(conn: sqlite3.Connection) -> None:
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(appointment_list_updates)").fetchall()
    }
    if "ingested_at" not in cols:
        conn.execute("ALTER TABLE appointment_list_updates ADD COLUMN ingested_at TEXT")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _copy_table(
    src: sqlite3.Connection,
    dest: sqlite3.Connection,
    table: str,
    *,
    replace: bool = False,
) -> int:
    tables = {
        row[0]
        for row in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if table not in tables:
        return 0
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        return 0
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    cols = [d[1] for d in src.execute(f"PRAGMA table_info({table})").fetchall()]
    dest_cols = [d[1] for d in dest.execute(f"PRAGMA table_info({table})").fetchall()]
    use_cols = [c for c in cols if c in dest_cols]
    if not use_cols:
        return 0
    placeholders = ",".join("?" * len(use_cols))
    col_sql = ",".join(use_cols)
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    n = 0
    for row in rows:
        values = [row[c] for c in use_cols]
        dest.execute(
            f"{verb} INTO {table} ({col_sql}) VALUES ({placeholders})",
            values,
        )
        n += 1
    return n


def migrate_list_heads_from_main(
    heads_conn: sqlite3.Connection,
    *,
    main_db_path: str | Path = DEFAULT_DB_PATH,
) -> dict[str, int]:
    """Pull list-heads tables out of tax_hr.db whenever they still exist there.

    Safe to call repeatedly: merges into ``list_heads.db`` then drops the
    tables from the main DB so long scans stop locking accounts/login.
    """
    ensure_list_heads_schema(heads_conn)
    main_path = Path(main_db_path)
    if not main_path.is_file():
        return {"heads": 0, "updates": 0, "skipped": 1}

    main = sqlite3.connect(str(main_path), timeout=60)
    main.row_factory = sqlite3.Row
    main.execute("PRAGMA busy_timeout=60000")
    try:
        tables = {
            row[0]
            for row in main.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "appointment_list_heads" not in tables and "appointment_list_updates" not in tables:
            return {"heads": 0, "updates": 0, "skipped": 1}
        ensure_list_heads_schema(main)
        n_heads = _copy_table(
            main, heads_conn, "appointment_list_heads", replace=True
        )
        n_upd = _copy_table(
            main, heads_conn, "appointment_list_updates", replace=True
        )
        heads_conn.commit()
        main.execute("DROP TABLE IF EXISTS appointment_list_heads")
        main.execute("DROP TABLE IF EXISTS appointment_list_updates")
        main.execute("DROP TABLE IF EXISTS appointment_list_pending")
        main.commit()
        logging.info(
            "reclaimed list heads into dedicated DB (heads=%s updates=%s)",
            n_heads,
            n_upd,
        )
        return {"heads": n_heads, "updates": n_upd, "skipped": 0}
    finally:
        main.close()


def connect_list_heads(
    db_path: str | Path | None = None,
    *,
    main_db_path: str | Path = DEFAULT_DB_PATH,
) -> sqlite3.Connection:
    """Open dedicated list-heads DB (not tax_hr.db)."""
    path = Path(db_path) if db_path else DEFAULT_LIST_HEADS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_list_heads_schema(conn)
    migrate_list_heads_from_main(conn, main_db_path=main_db_path)
    conn.commit()
    return conn


@dataclass(frozen=True)
class ListHead:
    rank: int
    title: str
    source_url: str
    published_on: str | None  # YYYY-MM-DD
    has_update: int = 0


def load_heads(conn: sqlite3.Connection, bureau_code: str) -> list[ListHead]:
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT rank, title, source_url, published_on, has_update
        FROM appointment_list_heads
        WHERE bureau_code = ?
        ORDER BY rank ASC
        """,
        (bureau_code,),
    ).fetchall()
    return [
        ListHead(
            rank=int(r["rank"]),
            title=r["title"] or "",
            source_url=r["source_url"] or "",
            published_on=(r["published_on"] or None),
            has_update=int(r["has_update"] or 0),
        )
        for r in rows
    ]


def heads_from_notices(conn: sqlite3.Connection, bureau_code: str, *, limit: int = 2) -> list[ListHead]:
    """Bootstrap baseline from already-ingested notices in **main** DB."""
    rows = conn.execute(
        """
        SELECT title, source_url,
               COALESCE(substr(published_at, 1, 10), issued_on, '') AS d
        FROM notices
        WHERE bureau_code = ?
        ORDER BY COALESCE(substr(published_at, 1, 10), issued_on, '') DESC, id DESC
        LIMIT ?
        """,
        (bureau_code, limit),
    ).fetchall()
    heads: list[ListHead] = []
    for i, row in enumerate(rows, start=1):
        day = (row["d"] or "").strip()[:10] or None
        heads.append(
            ListHead(
                rank=i,
                title=row["title"] or "",
                source_url=row["source_url"] or "",
                published_on=day,
                has_update=0,
            )
        )
    return heads


def replace_heads(
    conn: sqlite3.Connection,
    bureau_code: str,
    heads: list[ListHead],
    *,
    has_update: bool,
) -> None:
    """Replace stored top-N heads for a bureau (keep at most 2)."""
    ensure_list_heads_schema(conn)
    checked = _now()
    conn.execute("DELETE FROM appointment_list_heads WHERE bureau_code = ?", (bureau_code,))
    for head in heads[:2]:
        conn.execute(
            """
            INSERT INTO appointment_list_heads(
                bureau_code, rank, title, source_url, published_on, has_update, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bureau_code,
                int(head.rank),
                head.title,
                head.source_url,
                head.published_on,
                1 if has_update else 0,
                checked,
            ),
        )


def replace_bureau_updates(
    conn: sqlite3.Connection,
    bureau_code: str,
    items: list[dict[str, Any]],
) -> None:
    """Replace *uningested* update rows for a bureau; keep already-ingested history."""
    ensure_list_heads_schema(conn)
    conn.execute(
        """
        DELETE FROM appointment_list_updates
        WHERE bureau_code = ? AND ingested_at IS NULL
        """,
        (bureau_code,),
    )
    discovered = _now()
    for item in items:
        url = (item.get("source_url") or "").strip()
        if not url:
            continue
        conn.execute(
            """
            INSERT INTO appointment_list_updates(
                bureau_code, source_url, title, published_on, discovered_at, ingested_at
            ) VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT(bureau_code, source_url) DO UPDATE SET
                title=excluded.title,
                published_on=excluded.published_on,
                discovered_at=excluded.discovered_at,
                ingested_at=NULL
            """,
            (
                bureau_code,
                url,
                item.get("title") or "",
                item.get("published_on"),
                discovered,
            ),
        )


def mark_updates_ingested(
    conn: sqlite3.Connection,
    bureau_codes: list[str] | None = None,
) -> int:
    """Mark waiting updates as ingested (keep rows; do not delete)."""
    ensure_list_heads_schema(conn)
    now = _now()
    if bureau_codes is None:
        cur = conn.execute(
            """
            UPDATE appointment_list_updates
            SET ingested_at = ?
            WHERE ingested_at IS NULL
            """,
            (now,),
        )
        return int(cur.rowcount or 0)
    if not bureau_codes:
        return 0
    placeholders = ",".join("?" * len(bureau_codes))
    cur = conn.execute(
        f"""
        UPDATE appointment_list_updates
        SET ingested_at = ?
        WHERE ingested_at IS NULL AND bureau_code IN ({placeholders})
        """,
        [now, *bureau_codes],
    )
    return int(cur.rowcount or 0)


def list_waiting_updates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Rows not yet batch-ingested into notices."""
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT bureau_code, source_url, title, published_on, discovered_at, ingested_at
        FROM appointment_list_updates
        WHERE ingested_at IS NULL
        ORDER BY bureau_code, published_on DESC, source_url
        """
    ).fetchall()
    return [dict(r) for r in rows]


def list_waiting_bureaus(conn: sqlite3.Connection) -> list[str]:
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT bureau_code
        FROM appointment_list_updates
        WHERE ingested_at IS NULL
        ORDER BY bureau_code
        """
    ).fetchall()
    return [r["bureau_code"] for r in rows]


def clear_update_tags(conn: sqlite3.Connection, bureau_codes: list[str] | None = None) -> int:
    ensure_list_heads_schema(conn)
    if bureau_codes is None:
        cur = conn.execute(
            "UPDATE appointment_list_heads SET has_update = 0 WHERE has_update != 0"
        )
        return int(cur.rowcount or 0)
    if not bureau_codes:
        return 0
    placeholders = ",".join("?" * len(bureau_codes))
    cur = conn.execute(
        f"""
        UPDATE appointment_list_heads
        SET has_update = 0
        WHERE has_update != 0 AND bureau_code IN ({placeholders})
        """,
        bureau_codes,
    )
    return int(cur.rowcount or 0)


def list_updated_bureaus(conn: sqlite3.Connection) -> list[str]:
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT bureau_code FROM appointment_list_heads WHERE has_update = 1
        UNION
        SELECT bureau_code FROM appointment_list_updates WHERE ingested_at IS NULL
        ORDER BY 1
        """
    ).fetchall()
    return [r[0] for r in rows]


def list_updated_heads(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT bureau_code, rank, title, source_url, published_on, checked_at
        FROM appointment_list_heads
        WHERE has_update = 1
        ORDER BY bureau_code, rank
        """
    ).fetchall()
    return [dict(r) for r in rows]


def newest_published_on(heads: list[ListHead]) -> str | None:
    if not heads:
        return None
    return heads[0].published_on


def is_newer_than(candidate: str | None, baseline: str | None) -> bool:
    """True only when candidate date is strictly after baseline (ISO YYYY-MM-DD)."""
    if not candidate:
        return False
    if not baseline:
        return True
    return candidate[:10] > baseline[:10]


def record_list_scan_failure(
    conn: sqlite3.Connection,
    bureau_code: str,
    *,
    list_url: str = "",
    error: str = "",
) -> None:
    """Mark a bureau list page as failed (upsert; bump retry_count)."""
    ensure_list_heads_schema(conn)
    now = _now()
    existing = conn.execute(
        "SELECT retry_count FROM appointment_list_scan_failures WHERE bureau_code = ?",
        (bureau_code,),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE appointment_list_scan_failures
            SET list_url = ?, error = ?, failed_at = ?, retry_count = ?, resolved_at = NULL
            WHERE bureau_code = ?
            """,
            (list_url or "", error or "", now, int(existing[0]) + 1, bureau_code),
        )
    else:
        conn.execute(
            """
            INSERT INTO appointment_list_scan_failures(
                bureau_code, list_url, error, failed_at, retry_count, resolved_at
            ) VALUES (?, ?, ?, ?, 0, NULL)
            """,
            (bureau_code, list_url or "", error or "", now),
        )


def clear_list_scan_failure(conn: sqlite3.Connection, bureau_code: str) -> None:
    """Mark failure resolved (keep row for audit) or no-op if none."""
    ensure_list_heads_schema(conn)
    conn.execute(
        """
        UPDATE appointment_list_scan_failures
        SET resolved_at = ?
        WHERE bureau_code = ? AND resolved_at IS NULL
        """,
        (_now(), bureau_code),
    )


def list_open_scan_failures(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_list_heads_schema(conn)
    rows = conn.execute(
        """
        SELECT bureau_code, list_url, error, failed_at, retry_count
        FROM appointment_list_scan_failures
        WHERE resolved_at IS NULL
        ORDER BY bureau_code
        """
    ).fetchall()
    return [dict(r) for r in rows]


# Back-compat aliases
replace_pending = replace_bureau_updates
list_pending = list_waiting_updates
list_pending_bureaus = list_waiting_bureaus


def clear_pending(conn: sqlite3.Connection, bureau_codes: list[str] | None = None) -> int:
    """Deprecated: marks ingested instead of deleting."""
    return mark_updates_ingested(conn, bureau_codes)
