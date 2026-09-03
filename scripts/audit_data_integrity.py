# -*- coding: utf-8 -*-
"""One-shot integrity / dirty-data audit for tax_hr.db."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites import ALL_SITES, get_site
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.search.display import infer_unit_category
from tax_platform.store.anomalies import list_anomalies, scan_anomalies
from tax_platform.store.schema import connect

DB = ROOT / "output" / "tax_hr.db"
OUT = ROOT / "output" / "_data_integrity_audit.json"


def q(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def main() -> None:
    conn = connect(DB, light=True)
    known_bureaus = {s.code for s in ALL_SITES}

    tables = [
        r["name"]
        for r in q(
            conn,
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
    ]
    counts = {t: one(conn, f"SELECT COUNT(*) FROM [{t}]") for t in tables}

    # --- referential integrity ---
    orphan_events = one(
        conn,
        """
        SELECT COUNT(*) FROM appointment_events e
        LEFT JOIN notices n ON n.id = e.notice_id
        WHERE n.id IS NULL
        """,
    )
    orphan_tenures = 0
    orphan_posts_identity = 0
    if "org_post_tenures" in counts:
        orphan_tenures = one(
            conn,
            """
            SELECT COUNT(*) FROM org_post_tenures t
            LEFT JOIN org_posts p ON p.id = t.post_id
            WHERE p.id IS NULL
            """,
        )
        orphan_posts_identity = one(
            conn,
            """
            SELECT COUNT(*) FROM org_post_tenures t
            WHERE t.identity_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM person_identities i WHERE i.id = t.identity_id
              )
            """,
        )

    orphan_name_keys = 0
    if "person_name_keys" in counts:
        orphan_name_keys = one(
            conn,
            """
            SELECT COUNT(*) FROM person_name_keys k
            WHERE NOT EXISTS (
              SELECT 1 FROM person_identities i WHERE i.id = k.identity_id
            )
            """,
        )

    # --- unknown / suspicious bureau codes ---
    def unknown_codes(table: str, col: str = "bureau_code") -> list[dict]:
        rows = q(conn, f"SELECT {col} AS code, COUNT(*) AS n FROM {table} GROUP BY {col}")
        out = []
        for r in rows:
            code = r["code"] or ""
            if code not in known_bureaus:
                out.append({"code": code, "n": r["n"], "table": table})
        return sorted(out, key=lambda x: -x["n"])

    unknown = []
    for table in ("notices", "appointment_events", "leader_duties", "persons"):
        if table in counts:
            unknown.extend(unknown_codes(table))

    # --- dirty person names ---
    bad_event_names = q(
        conn,
        """
        SELECT person_name, COUNT(*) AS n
        FROM appointment_events
        GROUP BY person_name
        HAVING COUNT(*) >= 1
        ORDER BY n DESC
        LIMIT 5000
        """,
    )
    implausible_events = [
        {"name": r["person_name"], "n": r["n"]}
        for r in bad_event_names
        if not is_plausible_person_name(r["person_name"])
    ]
    implausible_events.sort(key=lambda x: -x["n"])

    bad_leaders = [
        {"name": r["person_name"], "bureau": r["bureau_code"], "id": r["id"]}
        for r in q(conn, "SELECT id, bureau_code, person_name FROM leader_duties")
        if not is_plausible_person_name(r["person_name"])
    ]
    bad_persons = [
        {"id": r["id"], "name": r["name"], "bureau": r["bureau_code"]}
        for r in q(conn, "SELECT id, name, bureau_code FROM persons")
        if not is_plausible_person_name(r["name"])
    ]

    # --- empty / noise fields ---
    empty_event_dept_title = one(
        conn,
        """
        SELECT COUNT(*) FROM appointment_events
        WHERE IFNULL(TRIM(department_raw),'') = ''
          AND IFNULL(TRIM(title_raw),'') = ''
        """,
    )
    undated_events = one(
        conn,
        "SELECT COUNT(*) FROM appointment_events WHERE IFNULL(TRIM(effective_on),'') = ''",
    )
    empty_notice_title = one(
        conn,
        "SELECT COUNT(*) FROM notices WHERE IFNULL(TRIM(title),'') = ''",
    )

    # --- duplicate-ish persons / leaders ---
    dup_person_ids = q(
        conn,
        """
        SELECT name, COUNT(*) AS n, GROUP_CONCAT(bureau_code) AS bureaus
        FROM persons
        GROUP BY name
        HAVING COUNT(*) > 8
        ORDER BY n DESC
        LIMIT 20
        """,
    )
    same_bureau_multi_person = one(
        conn,
        """
        SELECT COUNT(*) FROM (
          SELECT bureau_code, name, COUNT(*) c FROM persons
          GROUP BY bureau_code, name HAVING c > 1
        )
        """,
    )

    # --- relationship coverage ---
    persons_no_events = one(
        conn,
        """
        SELECT COUNT(*) FROM persons p
        WHERE NOT EXISTS (
          SELECT 1 FROM appointment_events e
          WHERE e.bureau_code = p.bureau_code AND e.person_name = p.name
        )
        AND NOT EXISTS (
          SELECT 1 FROM appointment_events e2 WHERE e2.person_name = p.name
        )
        """,
    )
    persons_no_leader = one(
        conn,
        """
        SELECT COUNT(*) FROM persons p
        WHERE NOT EXISTS (
          SELECT 1 FROM leader_duties l
          WHERE l.bureau_code = p.bureau_code AND l.person_name = p.name
        )
        """,
    )
    leaders_no_person = one(
        conn,
        """
        SELECT COUNT(*) FROM leader_duties l
        WHERE NOT EXISTS (
          SELECT 1 FROM persons p
          WHERE p.bureau_code = l.bureau_code AND p.name = l.person_name
        )
        """,
    )
    notices_no_events = one(
        conn,
        """
        SELECT COUNT(*) FROM notices n
        WHERE NOT EXISTS (
          SELECT 1 FROM appointment_events e WHERE e.notice_id = n.id
        )
        """,
    )
    events_missing_person_row = one(
        conn,
        """
        SELECT COUNT(DISTINCT e.bureau_code || ':' || e.person_name)
        FROM appointment_events e
        WHERE NOT EXISTS (
          SELECT 1 FROM persons p
          WHERE p.bureau_code = e.bureau_code AND p.name = e.person_name
        )
        """,
    )

    # --- is_current sanity ---
    current_no_title = one(
        conn,
        """
        SELECT COUNT(*) FROM persons
        WHERE COALESCE(is_current,0)=1
          AND IFNULL(TRIM(title_current),'')=''
          AND IFNULL(TRIM(department_current),'')=''
        """,
    )
    multi_current_same_name = q(
        conn,
        """
        SELECT name, COUNT(*) AS n
        FROM persons
        WHERE COALESCE(is_current,0)=1
        GROUP BY name
        HAVING COUNT(*) > 3
        ORDER BY n DESC
        LIMIT 15
        """,
    )

    # --- STA category sample distribution among persons ---
    sta_cat = Counter()
    for r in q(
        conn,
        """
        SELECT name, title_current, department_current
        FROM persons WHERE bureau_code='sta'
        """,
    ):
        cat = infer_unit_category(
            "sta",
            {
                "title": r["title_current"],
                "department": r["department_current"],
                "unit": None,
            },
        )
        sta_cat[cat or "(empty)"] += 1

    # --- posts multi-incumbent oddities ---
    multi_incumbent = []
    if "org_post_tenures" in counts:
        multi_incumbent = q(
            conn,
            """
            SELECT p.bureau_code, p.department, p.title, COUNT(*) AS n
            FROM org_post_tenures t
            JOIN org_posts p ON p.id = t.post_id
            WHERE t.is_current = 1
            GROUP BY t.post_id
            HAVING COUNT(*) > 5
            ORDER BY n DESC
            LIMIT 15
            """,
        )

    # --- semantic near-dup events (same person+day+action+dept+title different url) ---
    near_dup_groups = one(
        conn,
        """
        SELECT COUNT(*) FROM (
          SELECT person_name, action,
                 COALESCE(substr(effective_on,1,10),''),
                 COALESCE(department_raw,''),
                 COALESCE(title_raw,''),
                 COUNT(DISTINCT source_url) AS urls
          FROM appointment_events
          GROUP BY 1,2,3,4,5
          HAVING urls > 1
        )
        """,
    )

    # --- built-in anomaly scan ---
    scan_stats = scan_anomalies(conn=conn)
    anomaly_list = list_anomalies(status="open", limit=5000, conn=conn)
    by_kind = Counter(i["kind"] for i in anomaly_list.get("items") or [])
    by_sev = Counter(i["severity"] for i in anomaly_list.get("items") or [])
    top_anomalies = (anomaly_list.get("items") or [])[:25]

    # severity ranking for findings
    findings = []

    def add(severity: str, area: str, title: str, detail: str, n: int | None = None):
        findings.append(
            {
                "severity": severity,
                "area": area,
                "title": title,
                "detail": detail,
                "n": n,
            }
        )

    if orphan_events:
        add("critical", "FK", "任免事件悬挂 notice_id", "appointment_events 找不到对应 notices", orphan_events)
    if orphan_tenures:
        add("critical", "FK", "岗位任期悬挂 post_id", "org_post_tenures 找不到 org_posts", orphan_tenures)
    if orphan_name_keys:
        add("warn", "FK", "identity 名键悬挂", "person_name_keys 无对应 person_identities", orphan_name_keys)
    if orphan_posts_identity:
        add("warn", "FK", "岗位任期 identity 悬挂", "org_post_tenures.identity_id 无效", orphan_posts_identity)

    unk_total = sum(x["n"] for x in unknown)
    if unk_total:
        add(
            "warn",
            "registry",
            "未注册 bureau_code",
            f"{len(unknown)} 种代码，合计 {unk_total} 行；常见: "
            + ", ".join(f"{u['code']}({u['n']})" for u in unknown[:8]),
            unk_total,
        )

    if implausible_events:
        add(
            "critical",
            "names",
            "任免事件疑似脏人名",
            f"{len(implausible_events)} 个不同名字；Top: "
            + ", ".join(f"{x['name']}×{x['n']}" for x in implausible_events[:8]),
            sum(x["n"] for x in implausible_events),
        )
    if bad_leaders:
        add("warn", "names", "领导简介疑似脏人名", f"{len(bad_leaders)} 条 leader_duties", len(bad_leaders))
    if bad_persons:
        add("warn", "names", "persons 疑似脏人名", f"{len(bad_persons)} 条", len(bad_persons))

    if empty_event_dept_title:
        add("info", "fields", "任免事件科室+职务皆空", "department_raw 与 title_raw 同时为空", empty_event_dept_title)
    if undated_events:
        add("info", "fields", "任免事件无生效日", "effective_on 为空", undated_events)
    if notices_no_events:
        add("info", "coverage", "公告无解析出事件", "notices 无关联 appointment_events", notices_no_events)
    if events_missing_person_row:
        add(
            "warn",
            "coverage",
            "事件人物未进 persons",
            "有 appointment_events 但无同局 persons 行",
            events_missing_person_row,
        )
    if leaders_no_person:
        add("info", "coverage", "领导简介未同步到 persons", "leader_duties 无对应 persons", leaders_no_person)
    if near_dup_groups:
        add(
            "warn",
            "dupes",
            "疑似语义重复任免",
            "同人同日同动作同科室职务但不同 source_url",
            near_dup_groups,
        )
    if current_no_title:
        add("info", "current", "现任但无职务/科室", "is_current=1 且 title/department 皆空", current_no_title)
    if multi_incumbent:
        add(
            "warn",
            "posts",
            "单岗位现任人数异常偏多",
            "Top: "
            + "; ".join(
                f"{r['bureau_code']}/{r['department']}/{r['title']}={r['n']}"
                for r in multi_incumbent[:5]
            ),
            len(multi_incumbent),
        )

    open_n = int(scan_stats.get("open_count") or 0)
    if open_n:
        top_kinds = ", ".join(f"{k}={v}" for k, v in by_kind.most_common(8))
        add("warn", "anomaly_scan", "内置异常扫描仍有 open 项", top_kinds, open_n)

    report = {
        "db": str(DB),
        "counts": counts,
        "integrity": {
            "orphan_events": orphan_events,
            "orphan_tenures": orphan_tenures,
            "orphan_name_keys": orphan_name_keys,
            "orphan_posts_identity": orphan_posts_identity,
            "same_bureau_multi_person_rows": same_bureau_multi_person,
            "persons_no_events": persons_no_events,
            "persons_no_leader": persons_no_leader,
            "leaders_no_person": leaders_no_person,
            "notices_no_events": notices_no_events,
            "events_missing_person_row": events_missing_person_row,
            "near_dup_event_groups": near_dup_groups,
            "empty_event_dept_title": empty_event_dept_title,
            "undated_events": undated_events,
            "empty_notice_title": empty_notice_title,
            "current_no_title": current_no_title,
        },
        "unknown_bureau_codes": unknown[:30],
        "implausible_event_names_top": implausible_events[:30],
        "implausible_leader_names": bad_leaders[:20],
        "implausible_person_names": bad_persons[:20],
        "frequent_same_name_persons": dup_person_ids,
        "multi_current_same_name": multi_current_same_name,
        "sta_person_categories": dict(sta_cat),
        "multi_incumbent_posts": multi_incumbent,
        "anomaly_scan": scan_stats,
        "anomaly_by_kind": dict(by_kind),
        "anomaly_by_severity": dict(by_sev),
        "top_anomalies": [
            {
                "kind": a.get("kind"),
                "severity": a.get("severity"),
                "bureau_code": a.get("bureau_code"),
                "person_name": a.get("person_name"),
                "message": a.get("message"),
            }
            for a in top_anomalies
        ],
        "findings": findings,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "findings": len(findings), "open_anomalies": open_n, "counts": counts}, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
