"""Latest appointment notices feed (notice-level, not person events)."""

from __future__ import annotations

import sqlite3
from typing import Any

from tax_platform.config.sites import get_site, list_sites
from tax_platform.crawler.text_clean import normalize_doc_no, normalize_notice_title
from tax_platform.search.display import bureau_codes_for_category, infer_unit_category
from tax_platform.store.schema import connect


def list_notices(
    *,
    org_level: str | None = None,
    bureau_code: str | None = None,
    unit_category: str | None = None,
    q: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """List notices ordered by decision/publish date descending."""
    owns = conn is None
    db = conn or connect()
    level_q = (org_level or "").strip()
    bureau_q = (bureau_code or "").strip()
    cat_q = (unit_category or "").strip()
    text_q = (q or "").strip()
    from_q = (date_from or "").strip()[:10]
    to_q = (date_to or "").strip()[:10]

    where = ["1=1"]
    params: list[Any] = []

    if bureau_q:
        where.append("n.bureau_code = ?")
        params.append(bureau_q)
    else:
        codes = bureau_codes_for_category(org_level=level_q or None, unit_category=cat_q or None)
        if codes is None and level_q:
            codes = [s.code for s in list_sites(level=level_q)]
        if codes is not None:
            if not codes:
                if owns:
                    db.close()
                return {"total": 0, "offset": offset, "limit": limit, "items": []}
            placeholders = ",".join("?" * len(codes))
            where.append(f"n.bureau_code IN ({placeholders})")
            params.extend(codes)

    if text_q:
        where.append("(n.title LIKE ? OR IFNULL(n.issuer, '') LIKE ? OR IFNULL(n.doc_no, '') LIKE ?)")
        like = f"%{text_q}%"
        params.extend([like, like, like])

    if from_q:
        where.append("COALESCE(n.issued_on, substr(n.published_at, 1, 10), '') >= ?")
        params.append(from_q)
    if to_q:
        where.append("COALESCE(n.issued_on, substr(n.published_at, 1, 10), '') <= ?")
        params.append(to_q)

    where_sql = " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) FROM notices n WHERE {where_sql}",
        params,
    ).fetchone()[0]

    # Page first, then aggregate event counts — avoids a correlated subquery
    # over every notice when notice_id is unindexed / large.
    rows = db.execute(
        f"""
        WITH page AS (
            SELECT
                n.id,
                n.bureau_code,
                n.title,
                n.source_url,
                n.published_at,
                n.doc_no,
                n.issuer,
                n.issued_on
            FROM notices n
            WHERE {where_sql}
            ORDER BY COALESCE(n.issued_on, substr(n.published_at, 1, 10), '') DESC,
                     n.id DESC
            LIMIT ? OFFSET ?
        )
        SELECT
            p.*,
            COALESCE(c.cnt, 0) AS event_count
        FROM page p
        LEFT JOIN (
            SELECT notice_id, COUNT(*) AS cnt
            FROM appointment_events
            WHERE notice_id IN (SELECT id FROM page)
            GROUP BY notice_id
        ) c ON c.notice_id = p.id
        ORDER BY COALESCE(p.issued_on, substr(p.published_at, 1, 10), '') DESC,
                 p.id DESC
        """,
        [*params, limit, offset],
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        bureau = row["bureau_code"]
        try:
            site = get_site(bureau)
            level, region, bureau_name = site.level, site.region, site.name
        except KeyError:
            level, region, bureau_name = "unknown", None, bureau

        items.append(
            {
                "id": row["id"],
                "bureau_code": bureau,
                "bureau_name": bureau_name,
                "org_level": level,
                "region": region,
                "unit_category": infer_unit_category(bureau),
                "title": normalize_notice_title(row["title"]) or row["title"],
                "source_url": row["source_url"],
                "published_at": row["published_at"],
                "issued_on": row["issued_on"],
                "doc_no": normalize_doc_no(row["doc_no"]),
                "issuer": row["issuer"],
                "event_count": int(row["event_count"] or 0),
                "sort_date": (row["issued_on"] or (row["published_at"] or "")[:10] or None),
            }
        )

    if owns:
        db.close()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }
