"""CLI: search people by title/department, and department↔leader linkage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.search.query import (
    departments_for_leader,
    leaders_for_department,
    lookup_department,
    search_people,
    suggest_departments,
    suggest_titles,
)
from tax_platform.store.schema import connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("output/tax_hr.db"))
    parser.add_argument("--title", default=None, help="职务关键词，如 副局长")
    parser.add_argument("--department", "--dept", default=None, help="科室关键词，如 政策法规")
    parser.add_argument("--name", default=None, help="人员姓名（可单独或与职务/科室组合）")
    parser.add_argument("--date-from", default=None, help="任职日起 YYYY-MM-DD")
    parser.add_argument("--date-to", default=None, help="任职日止 YYYY-MM-DD")
    parser.add_argument(
        "--level",
        choices=["headquarters", "province", "city", "district"],
        default=None,
        help="限定层级",
    )
    parser.add_argument("--bureau", default=None, help="限定 bureau_code")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--link",
        action="store_true",
        help="科室一站式：分管领导 + 任职人员（需 --department）",
    )
    parser.add_argument("--suggest-dept", metavar="Q", default=None, help="从映射表联想科室")
    parser.add_argument("--suggest-title", metavar="Q", default=None, help="从映射表联想职务")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    conn = connect(args.db)

    if args.suggest_dept is not None:
        rows = suggest_departments(args.suggest_dept, org_level=args.level, conn=conn)
        _print_rows(rows, args.json, kind="dept")
        conn.close()
        return

    if args.suggest_title is not None:
        rows = suggest_titles(args.suggest_title, org_level=args.level, conn=conn)
        _print_rows(rows, args.json, kind="title")
        conn.close()
        return

    if args.name and not args.department and not args.title and not args.link:
        # Prefer search_people so appointments + date filters apply.
        results = search_people(
            name=args.name,
            org_level=args.level,
            bureau_code=args.bureau,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            conn=conn,
        )
        if args.json:
            print(json.dumps([_slim(r) for r in results], ensure_ascii=False, indent=2))
        else:
            print(f"hits: {len(results)}")
            for r in results:
                _print_person(r)
            # Also show 分管 if this name is a leader
            for row in departments_for_leader(args.name, bureau_code=args.bureau, conn=conn):
                print(f"\n分管科室 [{row['bureau_code']}]: {'、'.join(row.get('departments') or []) or '（无）'}")
        conn.close()
        return

    if args.link:
        if not args.department:
            parser.error("--link 需要同时提供 --department")
        result = lookup_department(
            args.department,
            org_level=args.level,
            bureau_code=args.bureau,
            staff_limit=args.limit,
            conn=conn,
        )
        if args.json:
            slim = {
                "department": result["department"],
                "org_level": result["org_level"],
                "bureau_code": result["bureau_code"],
                "supervisor_count": result["supervisor_count"],
                "staff_count": result["staff_count"],
                "supervising_leaders": result["supervising_leaders"],
                "staff": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "bureau_code": s["bureau_code"],
                        "roles": s.get("roles"),
                        "current": s.get("current"),
                        "appointments": s.get("appointments"),
                    }
                    for s in result["staff"]
                ],
            }
            print(json.dumps(slim, ensure_ascii=False, indent=2))
        else:
            print(f"科室: {result['department']}")
            print(f"分管领导: {result['supervisor_count']}")
            for lead in result["supervising_leaders"]:
                matched = "、".join(lead.get("matched_departments") or [])
                print(
                    f"  · {lead['name']}  [{lead['bureau_code']}]  "
                    f"{lead.get('title_raw') or ''}  匹配:{matched}"
                )
            print(f"任职人员: {result['staff_count']}（展示 {len(result['staff'])}）")
            for s in result["staff"]:
                cur = s.get("current") or {}
                print(
                    f"  · {s['name']}  [{s['bureau_code']}]  "
                    f"{cur.get('title') or ''} {cur.get('department') or ''}"
                )
        conn.close()
        return

    if args.department and not args.title and not args.link:
        # Default department search: show supervisors first, then staff via roles.
        leaders = leaders_for_department(
            args.department,
            org_level=args.level,
            bureau_code=args.bureau,
            limit=args.limit,
            conn=conn,
        )
        results = search_people(
            department=args.department,
            name=args.name,
            org_level=args.level,
            bureau_code=args.bureau,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
            conn=conn,
        )
        if args.json:
            print(
                json.dumps(
                    {"supervising_leaders": leaders, "people": results},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        else:
            print(f"分管领导: {len(leaders)}")
            for lead in leaders:
                print(
                    f"  · {lead['name']}  [{lead['bureau_code']}/{lead['org_level']}]  "
                    f"{lead.get('title_raw') or ''}"
                )
                print(f"    匹配科室: {'、'.join(lead.get('matched_departments') or [])}")
            staff = [r for r in results if "appointee" in r.get("roles", [])]
            print(f"\n任职命中: {len(staff)}（总命中 {len(results)}）")
            for r in staff[: args.limit]:
                print(f"\n{r['name']}  [{r['bureau_code']}/{r['org_level']}]  roles={r.get('roles')}")
                cur = r.get("current")
                if cur:
                    print(f"  current: {cur.get('title')} / {cur.get('department')}")
                for ev in r["appointments"][:3]:
                    when = ev.get("effective_on") or "?"
                    print(
                        f"  {when}  {ev.get('action')}  "
                        f"{ev.get('title_raw') or ''}  {ev.get('department_raw') or ''}"
                    )
        conn.close()
        return

    if not args.title and not args.department and not args.name:
        parser.error(
            "需要 --title / --department / --name，或 --suggest-dept / --suggest-title / --link"
        )

    results = search_people(
        title=args.title,
        department=args.department,
        name=args.name,
        org_level=args.level,
        bureau_code=args.bureau,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
        conn=conn,
    )
    if args.json:
        print(json.dumps([_slim(r) for r in results], ensure_ascii=False, indent=2))
    else:
        print(f"hits: {len(results)}")
        for r in results:
            _print_person(r)
    conn.close()


def _slim(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "bureau_code": r["bureau_code"],
        "org_level": r["org_level"],
        "roles": r.get("roles"),
        "match_reasons": r["match_reasons"],
        "supervised_departments": r.get("supervised_departments"),
        "current": r.get("current"),
        "appointments": r["appointments"],
    }


def _print_person(r: dict) -> None:
    print(f"\n{r['name']}  [{r['bureau_code']}/{r['org_level']}]  roles={r.get('roles')}")
    print(f"  match: {'; '.join((r.get('match_reasons') or [])[:3])}")
    cur = r.get("current")
    if cur:
        print(f"  current: {cur.get('title')} / {cur.get('department')}")
    for ev in (r.get("appointments") or [])[:5]:
        when = ev.get("effective_on") or "?"
        print(
            f"  {when}  {ev.get('action')}  "
            f"{ev.get('title_raw') or ''}  "
            f"{ev.get('department_raw') or ''}"
        )
    if (r.get("appointment_count") or 0) > 5:
        print(f"  ... +{r['appointment_count'] - 5} more")


def _print_rows(rows: list, as_json: bool, *, kind: str) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if kind == "dept":
        for row in rows:
            print(
                f"{row['canonical_name']}\t{row['org_level']}\t"
                f"{row['kind']}\tn={row['source_count']}"
            )
    else:
        for row in rows:
            print(f"{row['canonical_title']}\t{row['org_level']}\tn={row['source_count']}")


if __name__ == "__main__":
    main()
