from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("output/tax_hr.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bureau_code TEXT NOT NULL,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    published_at TEXT,
    doc_no TEXT,
    issuer TEXT,
    issued_on TEXT,
    raw_text TEXT
);

CREATE TABLE IF NOT EXISTS appointment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id INTEGER NOT NULL,
    bureau_code TEXT NOT NULL,
    person_name TEXT NOT NULL,
    action TEXT NOT NULL,
    bureau_name TEXT,
    department_raw TEXT,
    title_raw TEXT,
    probation_years INTEGER,
    effective_on TEXT,
    notice_title TEXT,
    raw_clause TEXT,
    source_url TEXT NOT NULL,
    FOREIGN KEY (notice_id) REFERENCES notices(id)
);

CREATE TABLE IF NOT EXISTS leader_duties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bureau_code TEXT NOT NULL,
    person_name TEXT NOT NULL,
    gender TEXT,
    ethnicity TEXT,
    title_raw TEXT,
    duty_summary TEXT,
    departments_json TEXT,
    source_url TEXT NOT NULL,
    UNIQUE (bureau_code, person_name, source_url)
);

CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    bureau_code TEXT NOT NULL,
    gender TEXT,
    title_current TEXT,
    source_leader_url TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    department_current TEXT,
    current_since TEXT,
    current_source_url TEXT
);

-- Hierarchy ↔ department catalog mined from appointments + leader duties.
-- Same canonical name may exist at multiple org_level rows (处 vs 科).
CREATE TABLE IF NOT EXISTS dept_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    org_level TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0,
    sample_bureaus TEXT,
    UNIQUE (canonical_name, org_level)
);

CREATE TABLE IF NOT EXISTS title_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_title TEXT NOT NULL,
    org_level TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (canonical_title, org_level)
);

-- PR6: detected data quality issues
CREATE TABLE IF NOT EXISTS data_anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warn',
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    bureau_code TEXT,
    person_name TEXT,
    message TEXT NOT NULL,
    evidence_json TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (kind, target_type, target_id)
);

-- PR6: manual correction audit log
CREATE TABLE IF NOT EXISTS manual_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    patch_json TEXT,
    note TEXT,
    anomaly_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (anomaly_id) REFERENCES data_anomalies(id)
);

CREATE INDEX IF NOT EXISTS idx_events_date ON appointment_events(effective_on);
CREATE INDEX IF NOT EXISTS idx_leaders_person ON leader_duties(person_name, bureau_code);
CREATE INDEX IF NOT EXISTS idx_dept_catalog_level ON dept_catalog(org_level, canonical_name);
CREATE INDEX IF NOT EXISTS idx_title_catalog_level ON title_catalog(org_level, canonical_title);
CREATE INDEX IF NOT EXISTS idx_anomalies_status ON data_anomalies(status, kind);
CREATE INDEX IF NOT EXISTS idx_corrections_target ON manual_corrections(target_type, target_id);
"""

_PERSONS_CURRENT_COLUMNS: list[tuple[str, str]] = [
    ("is_current", "INTEGER NOT NULL DEFAULT 0"),
    ("department_current", "TEXT"),
    ("current_since", "TEXT"),
    ("current_source_url", "TEXT"),
]


def _dedupe_appointment_events(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM appointment_events
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM appointment_events
            GROUP BY source_url, person_name, action, COALESCE(raw_clause, '')
        )
        """
    )


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    _dedupe_appointment_events(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique
            ON appointment_events(source_url, person_name, action, COALESCE(raw_clause, ''))
        """
    )
    # After migrate: old DBs may lack is_current until ALTER runs.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_persons_current
            ON persons(is_current, bureau_code)
        """
    )


def _migrate_persons_current(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(persons)")}
    for name, decl in _PERSONS_CURRENT_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE persons ADD COLUMN {name} {decl}")


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate_persons_current(conn)
    _ensure_indexes(conn)
    from tax_platform.config.sites import ALL_SITES
    from tax_platform.store.identity import ensure_identity_schema, sync_person_identities
    from tax_platform.store.org_units import ensure_org_units_schema, sync_org_units_from_sites
    from tax_platform.store.posts import ensure_posts_schema

    ensure_org_units_schema(conn)
    existing = conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0]
    if existing != len(ALL_SITES):
        sync_org_units_from_sites(conn, force=True)

    ensure_identity_schema(conn)
    sync_person_identities(conn)
    ensure_posts_schema(conn)

    conn.commit()
    return conn
