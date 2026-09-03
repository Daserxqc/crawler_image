"""Org posts + tenures rebuilt from appointment_events."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from tax_platform.config.sites import get_site, list_sites
from tax_platform.normalize.change import classify_change
from tax_platform.normalize.department import clean_department_name
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.search.display import bureau_codes_for_category, infer_unit_category
from tax_platform.store.identity import ensure_identity_for_person, ensure_identity_schema

_HQ_UNIT_CATEGORIES = frozenset({"internal", "direct", "dispatched"})

POSTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS org_posts (
    id TEXT PRIMARY KEY,
    bureau_code TEXT NOT NULL,
    department TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_org_posts_key
    ON org_posts(bureau_code, department, title);

CREATE INDEX IF NOT EXISTS idx_org_posts_bureau
    ON org_posts(bureau_code);

CREATE TABLE IF NOT EXISTS org_post_tenures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    identity_id TEXT,
    started_on TEXT,
    ended_on TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    source_url TEXT,
    change_type TEXT,
    end_change_type TEXT,
    FOREIGN KEY (post_id) REFERENCES org_posts(id)
);

CREATE INDEX IF NOT EXISTS idx_org_post_tenures_post
    ON org_post_tenures(post_id, is_current);
CREATE INDEX IF NOT EXISTS idx_org_post_tenures_person
    ON org_post_tenures(person_name);
CREATE INDEX IF NOT EXISTS idx_org_post_tenures_identity
    ON org_post_tenures(identity_id);
"""


def ensure_posts_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(POSTS_SCHEMA)


def post_id(bureau_code: str, department: str, title: str = "") -> str:
    key = f"{bureau_code}|{department}|{title}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _post_unit_label(bureau_code: str, bureau_name: str | None) -> str:
    """Short sub-unit label when the event targets a bureau below ``bureau_code``."""
    bn = (bureau_name or "").strip()
    if not bn or "税务局" not in bn:
        return ""
    try:
        from tax_platform.config.sites import get_site
        from tax_platform.search.display import short_bureau_name

        site = get_site(bureau_code)
        parent = site.name
        short = short_bureau_name(bn, parent_name=parent) or bn
        if bn == parent or short == short_bureau_name(parent):
            return ""
        return short
    except KeyError:
        from tax_platform.search.display import short_bureau_name

        return short_bureau_name(bn) or bn


def post_department_key(
    bureau_code: str,
    department_raw: str | None,
    bureau_name: str | None = None,
) -> str:
    """Department key for a post, scoped to sub-bureau when clause names one."""
    dept = _norm_dept(department_raw)
    unit = _post_unit_label(bureau_code, bureau_name)
    if unit and dept:
        return f"{unit} · {dept}"
    if unit:
        return unit
    return dept


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_dept(raw: str | None) -> str:
    return clean_department_name(raw or "") or (raw or "").strip()


def _dept_compact(raw: str | None) -> str:
    """Whitespace-insensitive department key for matching stored ``unit · dept`` labels."""
    return re.sub(r"\s+", "", raw or "")


def _title_allows_multiple_holders(title: str | None) -> bool:
    """副职 / 职级岗可多人同时在任，不做「接任」闭链。"""
    t = (title or "").strip()
    if not t:
        return False
    head = t.split("、")[0]
    if "副" in head:
        return True
    return any(
        x in head
        for x in ("主办", "调研员", "巡视员", "委员", "成员", "助理")
    )


def _append_past_row(past_rows: list, item: dict, **extra) -> None:
    past_rows.append({**item, **extra})


def rebuild_org_posts(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild org_posts / org_post_tenures from appointment_events.

    Post key = bureau + scoped department + title_raw.

    When a city notice appoints several district bureau chiefs, ``bureau_name``
    on each event distinguishes 临河区 / 乌拉特后旗 / … so they are not merged
    into one post with fake same-day 历任.
    """
    ensure_posts_schema(conn)
    ensure_identity_schema(conn)
    conn.execute("DELETE FROM org_post_tenures")
    conn.execute("DELETE FROM org_posts")

    rows = conn.execute(
        """
        SELECT * FROM appointment_events
        ORDER BY bureau_code, COALESCE(effective_on, '') ASC, id ASC
        """
    ).fetchall()

    # post_id -> {name -> open tenure dict}
    open_by_post: dict[str, dict[str, dict[str, Any]]] = {}
    posts_meta: dict[str, tuple[str, str, str]] = {}
    past_rows: list[dict[str, Any]] = []
    seen_event_keys: set[tuple[str, str, str, str, str, str]] = set()
    now = _now()

    for row in rows:
        name = row["person_name"]
        if not is_plausible_person_name(name):
            continue
        bureau = row["bureau_code"]
        dept = post_department_key(bureau, row["department_raw"], row["bureau_name"])
        title = (row["title_raw"] or "").strip()
        if not dept:
            continue
        pid = post_id(bureau, dept, title)
        posts_meta[pid] = (bureau, dept, title)
        open_tenures = open_by_post.setdefault(pid, {})

        ctype = classify_change(
            action=row["action"],
            title_raw=row["title_raw"],
            department_raw=row["department_raw"],
            notice_title=row["notice_title"],
            raw_clause=row["raw_clause"],
            previous_title=(open_tenures.get(name) or {}).get("title"),
            previous_department=(open_tenures.get(name) or {}).get("department"),
        )

        if ctype.value in {"dismiss", "retire"}:
            prev = open_tenures.pop(name, None)
            if prev:
                _append_past_row(
                    past_rows,
                    prev,
                    post_id=pid,
                    ended_on=row["effective_on"],
                    end_change_type=ctype.value,
                    is_current=0,
                )
            continue

        # Skip duplicate appoint rows (http/https twin notices, same day).
        event_key = (
            bureau,
            name,
            row["action"],
            dept,
            title,
            row["effective_on"] or "",
        )
        if event_key in seen_event_keys:
            continue
        seen_event_keys.add(event_key)

        # Singleton posts (局长/科长): new appoint closes prior holder.
        if not _title_allows_multiple_holders(title):
            for other_name in list(open_tenures.keys()):
                if other_name == name:
                    continue
                other = open_tenures.pop(other_name)
                _append_past_row(
                    past_rows,
                    other,
                    post_id=pid,
                    ended_on=row["effective_on"],
                    end_change_type="succeeded",
                    is_current=0,
                )

        prev = open_tenures.get(name)
        if prev and prev.get("started_on") != row["effective_on"]:
            _append_past_row(
                past_rows,
                prev,
                post_id=pid,
                ended_on=row["effective_on"],
                end_change_type="replaced",
                is_current=0,
            )
        open_tenures[name] = {
            "person_name": name,
            "title": row["title_raw"],
            "department": row["department_raw"],
            "started_on": row["effective_on"],
            "source_url": row["source_url"],
            "change_type": ctype.value,
            "bureau_code": bureau,
        }

    for pid, (bureau, dept, title) in posts_meta.items():
        conn.execute(
            """
            INSERT INTO org_posts (id, bureau_code, department, title, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pid, bureau, dept, title, now),
        )

    def _insert_tenure(item: dict[str, Any], *, is_current: int) -> None:
        bureau = item.get("bureau_code") or ""
        name = item["person_name"]
        iid = None
        if bureau and name:
            iid = ensure_identity_for_person(conn, bureau_code=bureau, name=name)
        conn.execute(
            """
            INSERT INTO org_post_tenures (
                post_id, person_name, identity_id, started_on, ended_on,
                is_current, source_url, change_type, end_change_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["post_id"],
                name,
                iid,
                item.get("started_on"),
                item.get("ended_on"),
                is_current,
                item.get("source_url"),
                item.get("change_type"),
                item.get("end_change_type"),
            ),
        )

    for item in past_rows:
        _insert_tenure(item, is_current=0)

    current_count = 0
    for pid, open_tenures in open_by_post.items():
        for name, prev in open_tenures.items():
            _insert_tenure(
                {
                    **prev,
                    "post_id": pid,
                    "ended_on": None,
                    "end_change_type": None,
                },
                is_current=1,
            )
            current_count += 1

    return {
        "posts": len(posts_meta),
        "tenures": len(past_rows) + current_count,
        "current": current_count,
    }


def posts_ready(conn: sqlite3.Connection) -> bool:
    ensure_posts_schema(conn)
    n = conn.execute("SELECT COUNT(*) FROM org_posts").fetchone()[0]
    return n > 0


_TITLE_LEVEL_TAG = {
    "总局": "headquarters",
    "省级": "province",
    "市局": "city",
    "区县": "district",
}


def _parse_title_query(title: str | None) -> tuple[str, str | None]:
    """Split catalog display labels like 副所长（区县） → (副所长, district)."""
    text = (title or "").strip()
    if not text:
        return "", None
    match = re.fullmatch(r"(.+?)（(总局|省级|市局|区县)）", text)
    if match:
        return match.group(1).strip(), _TITLE_LEVEL_TAG.get(match.group(2))
    return text, None


def search_org_posts(
    *,
    department: str | None = None,
    title: str | None = None,
    bureau_code: str | None = None,
    org_level: str | None = None,
    unit_category: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Browse/search posts by department / title with optional bureau or level."""
    ensure_posts_schema(conn)
    dept_q = (clean_department_name(department) or (department or "").strip()) if department else ""
    title_q, title_level = _parse_title_query(title)
    bureau_q = (bureau_code or "").strip()
    cat_q = (unit_category or "").strip()
    level_q = (org_level or "").strip() or (title_level or "")
    hq_cat = cat_q in _HQ_UNIT_CATEGORIES
    offset = max(0, int(offset or 0))
    limit = int(limit or 0)

    if not dept_q and not title_q and not bureau_q and not cat_q:
        raise ValueError("请至少填写科室、职务，或选择具体单位")

    where: list[str] = ["1=1"]
    params: list[Any] = []

    if bureau_q:
        where.append("p.bureau_code = ?")
        params.append(bureau_q)
    else:
        codes = bureau_codes_for_category(
            org_level=level_q or None, unit_category=cat_q or None
        )
        if codes is None and level_q:
            codes = [s.code for s in list_sites() if s.level == level_q]
        if codes is not None:
            if not codes:
                return {
                    "total": 0,
                    "offset": offset,
                    "limit": limit,
                    "items": [],
                    "department": dept_q or None,
                    "title": title_q or None,
                    "bureau_code": bureau_q or None,
                    "org_level": level_q or None,
                    "unit_category": cat_q or None,
                }
            placeholders = ",".join("?" * len(codes))
            where.append(f"p.bureau_code IN ({placeholders})")
            params.extend(codes)

    if dept_q:
        # Stored keys look like 「武隆区 · 白马税务所」; clean_department_name strips
        # spaces so equality/LIKE on the cleaned form misses. Compare compacted.
        where.append(
            "(replace(replace(p.department, ' ', ''), '　', '') = ? "
            "OR replace(replace(p.department, ' ', ''), '　', '') LIKE ?)"
        )
        compact = _dept_compact(dept_q)
        params.extend([compact, f"%{compact}%"])
    if title_q:
        where.append("p.title LIKE ?")
        params.append(f"%{title_q}%")

    where_sql = " AND ".join(where)

    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        bureau = row["bureau_code"]
        try:
            site = get_site(bureau)
            level, region, bureau_name = site.level, site.region, site.name
        except KeyError:
            level, region, bureau_name = "unknown", None, bureau

        def _split_names(raw: str | None, cap: int = 6) -> list[str]:
            if not raw:
                return []
            seen: list[str] = []
            for part in str(raw).split("、"):
                name = part.strip()
                if name and name not in seen:
                    seen.append(name)
                if len(seen) >= cap:
                    break
            return seen

        return {
            "post_id": row["post_id"],
            "bureau_code": bureau,
            "bureau_name": bureau_name,
            "org_level": level,
            "region": region,
            "unit_category": infer_unit_category(
                bureau,
                {"department": row["department"], "title": row["title"] or ""},
            ),
            "department": row["department"],
            "title": row["title"] or "",
            "incumbent_count": int(row["incumbent_count"] or 0),
            "past_count": int(row["past_count"] or 0),
            "incumbents": _split_names(row["incumbent_names"]),
            "past": _split_names(row["past_names"]),
        }

    select_sql = f"""
        SELECT
            p.id AS post_id,
            p.bureau_code,
            p.department,
            p.title,
            SUM(CASE WHEN t.is_current = 1 THEN 1 ELSE 0 END) AS incumbent_count,
            SUM(CASE WHEN t.is_current = 0 THEN 1 ELSE 0 END) AS past_count,
            GROUP_CONCAT(
                CASE WHEN t.is_current = 1 THEN t.person_name END, '、'
            ) AS incumbent_names,
            GROUP_CONCAT(
                CASE WHEN t.is_current = 0 THEN t.person_name END, '、'
            ) AS past_names
        FROM org_posts p
        LEFT JOIN org_post_tenures t ON t.post_id = p.id
        WHERE {where_sql}
        GROUP BY p.id
        ORDER BY past_count DESC, incumbent_count DESC, p.bureau_code, p.department, p.title
    """

    # 内设/直属/派出 are person/post text labels under bureau=sta — filter after classify.
    if hq_cat:
        matched: list[dict[str, Any]] = []
        for row in conn.execute(select_sql, params):
            item = _row_to_item(row)
            if item.get("unit_category") != cat_q:
                continue
            matched.append(item)
        total = len(matched)
        page_limit = limit if limit > 0 else total
        items = matched[offset : offset + page_limit] if page_limit else matched[offset:]
    else:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM org_posts p WHERE {where_sql}",
                params,
            ).fetchone()[0]
        )
        page_limit = limit if limit > 0 else min(total, 2000)
        rows = conn.execute(
            select_sql + " LIMIT ? OFFSET ?",
            [*params, page_limit, offset],
        ).fetchall()
        items = [_row_to_item(row) for row in rows]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
        "department": dept_q or None,
        "title": title_q or None,
        "bureau_code": bureau_q or None,
        "org_level": level_q or None,
        "unit_category": cat_q or None,
    }


def post_archive_from_db(
    *,
    bureau_code: str,
    department: str,
    title: str | None = None,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Serve post archive from org_posts when rebuilt; None if no matching posts."""
    ensure_posts_schema(conn)
    dept_q = clean_department_name(department) or department.strip()
    title_q = (title or "").strip()
    if not bureau_code or not dept_q:
        raise ValueError("bureau_code and department are required")
    dept_compact = _dept_compact(dept_q)

    if title_q:
        post_rows = conn.execute(
            """
            SELECT * FROM org_posts
            WHERE bureau_code = ?
              AND (
                replace(replace(department, ' ', ''), '　', '') = ?
                OR replace(replace(department, ' ', ''), '　', '') LIKE ?
              )
              AND (title = ? OR (title LIKE ? AND title NOT LIKE ?))
            """,
            (
                bureau_code,
                dept_compact,
                f"%{dept_compact}%",
                title_q,
                f"%{title_q}%",
                f"%副{title_q}%",
            ),
        ).fetchall()
        if len(post_rows) > 1:
            exact = [
                r
                for r in post_rows
                if _dept_compact(r["department"]) == dept_compact
                and (r["title"] or "") == title_q
            ]
            if exact:
                post_rows = exact
            else:
                exact_title = [r for r in post_rows if (r["title"] or "") == title_q]
                if exact_title:
                    post_rows = exact_title
    else:
        # department match: exact cleaned dept OR department contains query (legacy soft match)
        post_rows = conn.execute(
            """
            SELECT * FROM org_posts
            WHERE bureau_code = ?
              AND (
                replace(replace(department, ' ', ''), '　', '') = ?
                OR replace(replace(department, ' ', ''), '　', '') LIKE ?
                OR ? LIKE '%' || replace(replace(department, ' ', ''), '　', '') || '%'
              )
            """,
            (bureau_code, dept_compact, f"%{dept_compact}%", dept_compact),
        ).fetchall()

    if not post_rows:
        return None

    post_ids = [r["id"] for r in post_rows]
    placeholders = ",".join("?" * len(post_ids))
    tenures = conn.execute(
        f"""
        SELECT * FROM org_post_tenures
        WHERE post_id IN ({placeholders})
        ORDER BY COALESCE(started_on, '') ASC, id ASC
        """,
        post_ids,
    ).fetchall()

    incumbents: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []

    for t in tenures:
        event = {
            "person_name": t["person_name"],
            "identity_id": t["identity_id"],
            "change_type": t["change_type"] or ("dismiss" if not t["is_current"] else "appoint"),
            "title": None,
            "department": None,
            "effective_on": t["ended_on"] if not t["is_current"] and t["ended_on"] else t["started_on"],
            "notice_title": None,
            "source_url": t["source_url"],
        }
        # attach title/department from post
        post = next(r for r in post_rows if r["id"] == t["post_id"])
        event["title"] = post["title"]
        event["department"] = post["department"]
        history.append(event)

        item = {
            "person_name": t["person_name"],
            "identity_id": t["identity_id"],
            "title": post["title"],
            "department": post["department"],
            "since": t["started_on"],
            "source_url": t["source_url"],
            "change_type": t["change_type"],
        }
        if t["is_current"]:
            incumbents.append(item)
        else:
            past.append(
                {
                    **item,
                    "ended_on": t["ended_on"],
                    "end_change_type": t["end_change_type"],
                }
            )

    history.reverse()
    try:
        site = get_site(bureau_code)
        level, region = site.level, site.region
    except KeyError:
        level, region = "unknown", None

    return {
        "bureau_code": bureau_code,
        "org_level": level,
        "region": region,
        "department": dept_q,
        "title": title_q or None,
        "incumbents": incumbents,
        "past": list(reversed(past)),
        "history": history,
        "incumbent_count": len(incumbents),
        "history_count": len(history),
        "source": "org_posts",
    }
