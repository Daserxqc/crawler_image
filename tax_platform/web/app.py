"""Public search HTTP API (PR7–PR9) + simple static UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from tax_platform.config.sites import list_sites
from tax_platform.config.sta_units import list_sta_units, unit_keywords
from tax_platform.search.changes import list_changes, post_archive
from tax_platform.search.notices import list_notices
from tax_platform.search.export import rows_from_profile, rows_from_search_hits, to_csv_bytes, to_xlsx_bytes
from tax_platform.search.query import (
    departments_for_leader,
    leaders_for_department,
    lookup_department,
    penetrate_department,
    search_people,
    search_people_page,
    suggest_departments,
    suggest_names,
    suggest_titles,
)
from tax_platform.store.anomalies import (
    apply_correction,
    ignore_anomaly,
    list_anomalies,
    list_corrections,
    scan_anomalies,
)
from tax_platform.store.posts import search_org_posts
from tax_platform.store.ingest import get_person_profile
from tax_platform.store.schema import DEFAULT_DB_PATH, connect

app = FastAPI(
    title="税局人事检索 API",
    version="0.9.1",
    description="PR6–PR9：异常修正 / 检索 / 穿透 / 履历 / 变动流 / 岗位档案 / 导出",
)

# Overridable in tests.
DB_PATH: Path = Path(DEFAULT_DB_PATH)
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"


def _db_path() -> Path:
    return Path(DB_PATH)


def _slim_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": hit["id"],
        "name": hit["name"],
        "bureau_code": hit["bureau_code"],
        "org_level": hit["org_level"],
        "roles": hit.get("roles") or [],
        "match_reasons": hit.get("match_reasons") or [],
        "supervised_departments": hit.get("supervised_departments") or [],
        "current": hit.get("current"),
        "unit_category": hit.get("unit_category"),
        "category_label": hit.get("category_label"),
        "unit_display": hit.get("unit_display"),
        "region_display": hit.get("region_display"),
        "title_display": hit.get("title_display"),
        "appointment_count": hit.get("appointment_count") or 0,
        "appointments": [
            {
                "bureau_code": ev.get("bureau_code"),
                "action": ev.get("action"),
                "title_raw": ev.get("title_raw"),
                "department_raw": ev.get("department_raw"),
                "bureau_name": ev.get("bureau_name"),
                "effective_on": ev.get("effective_on"),
                "notice_title": ev.get("notice_title"),
                "source_url": ev.get("source_url"),
            }
            for ev in (hit.get("appointments") or [])
        ],
    }


@app.get("/api/health")
def health() -> dict[str, Any]:
    path = _db_path()
    return {"ok": True, "db": str(path), "db_exists": path.exists()}


def _html(path: Path) -> FileResponse:
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/")
def ui_index() -> FileResponse:
    return _html(STATIC_DIR / "index.html")


@app.get("/changes")
def ui_changes() -> FileResponse:
    return _html(STATIC_DIR / "changes.html")


@app.get("/notices")
def ui_notices() -> FileResponse:
    return _html(STATIC_DIR / "notices.html")


@app.get("/departments")
def ui_departments() -> FileResponse:
    return _html(STATIC_DIR / "departments.html")


@app.get("/posts")
def ui_posts() -> FileResponse:
    return _html(STATIC_DIR / "posts.html")


@app.get("/posts/view")
def ui_posts_view() -> FileResponse:
    return _html(STATIC_DIR / "post_view.html")


@app.get("/people/{person_id:path}")
def ui_person(person_id: str) -> FileResponse:
    """SPA-style: any /people/... path serves the profile shell."""
    _ = person_id
    return _html(STATIC_DIR / "person.html")


@app.get("/api/meta/levels")
def meta_levels() -> dict[str, Any]:
    return {
        "levels": [
            {"id": "headquarters", "label": "总局"},
            {"id": "province", "label": "省局"},
            {"id": "city", "label": "市局"},
            {"id": "district", "label": "区县局"},
        ]
    }


@app.get("/api/meta/units")
def meta_units(
    category: str | None = Query(None, description="internal|direct|dispatched"),
) -> dict[str, Any]:
    """总局层面「具体地区」：机关司局 + 直属事业单位。"""
    return {"items": list_sta_units(category)}


@app.get("/api/meta/bureaus")
def meta_bureaus(
    level: str | None = Query(None, description="headquarters|province|city|district"),
    parent: str | None = Query(None, alias="parent_code", description="上级单位 code"),
    region: str | None = Query(None, description="地区名，如 上海市"),
) -> dict[str, Any]:
    """单位主数据（org_units）；库空或未同步时回退站点配置。"""
    from tax_platform.store.org_units import list_org_units, sync_org_units_from_sites

    conn = connect(_db_path(), light=True)
    try:
        try:
            empty = conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0] == 0
        except Exception:
            empty = True
        if empty:
            # Only sync when the table is missing/empty — not on every page load.
            conn.close()
            conn = connect(_db_path())
            sync_org_units_from_sites(conn, force=False)
            if conn.execute("SELECT COUNT(*) FROM org_units").fetchone()[0] == 0:
                sync_org_units_from_sites(conn, force=True)
                conn.commit()
        items = list_org_units(conn, level=level, parent_code=parent, region=region)
        if items:
            return {
                "items": [
                    {
                        "code": u["code"],
                        "name": u["name"],
                        "region": u.get("region"),
                        "level": u["level"],
                        "parent_code": u.get("parent_code"),
                    }
                    for u in items
                ],
                "source": "org_units",
            }
    finally:
        conn.close()

    sites = list_sites(level)
    if parent:
        sites = [s for s in sites if s.parent_code == parent]
    if region:
        sites = [s for s in sites if s.region == region]
    return {
        "items": [
            {
                "code": s.code,
                "name": s.name,
                "region": s.region,
                "level": s.level,
                "parent_code": s.parent_code,
            }
            for s in sites
        ],
        "source": "config",
    }


@app.get("/api/meta/public-base")
def meta_public_base(request: Request) -> dict[str, Any]:
    """LAN / share base URL for invite links (not loopback).

    Override with env ``TAX_HR_PUBLIC_BASE`` e.g. ``http://192.168.90.231:8000``.
    """
    import os
    import socket

    env = (os.environ.get("TAX_HR_PUBLIC_BASE") or "").strip().rstrip("/")
    detected = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            port = request.url.port or 8000
            detected = f"http://{ip}:{port}"
    except OSError:
        detected = ""
    request_origin = str(request.base_url).rstrip("/")
    # Prefer env, then non-loopback request host, then UDP-detected LAN IP.
    host = (request.url.hostname or "").lower()
    if env:
        base = env
        source = "env"
    elif host and host not in {"127.0.0.1", "localhost"}:
        base = request_origin
        source = "request"
    elif detected:
        base = detected
        source = "detected"
    else:
        base = request_origin
        source = "fallback"
    return {"base": base, "detected": detected, "source": source}


@app.get("/api/meta/summary")
def meta_summary() -> dict[str, Any]:
    """首页概览数字（供 UI 统计条使用）。"""
    path = _db_path()
    if not path.exists():
        return {
            "persons": 0,
            "appointment_events": 0,
            "notices": 0,
            "bureaus": 0,
            "departments": 0,
            "date_span": None,
        }
    conn = connect(path)
    try:
        persons = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM appointment_events").fetchone()[0]
        notices = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
        bureaus = conn.execute("SELECT COUNT(DISTINCT bureau_code) FROM persons").fetchone()[0]
        departments = conn.execute("SELECT COUNT(*) FROM dept_catalog").fetchone()[0]
        row = conn.execute(
            "SELECT MIN(effective_on), MAX(effective_on) FROM appointment_events "
            "WHERE effective_on IS NOT NULL AND TRIM(effective_on) != ''"
        ).fetchone()
        date_span = None
        if row and row[0]:
            date_span = {"from": row[0], "to": row[1]}
    finally:
        conn.close()
    return {
        "persons": persons,
        "appointment_events": events,
        "notices": notices,
        "bureaus": bureaus,
        "departments": departments,
        "date_span": date_span,
    }


@app.get("/api/departments/suggest")
def api_suggest_departments(
    q: str = Query("", description="科室关键词"),
    level: str | None = Query(None, alias="org_level"),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    """层级-科室联动下拉数据。"""
    conn = connect(_db_path())
    try:
        items = suggest_departments(q, org_level=level, limit=limit, conn=conn)
    finally:
        conn.close()
    return {"items": items}


@app.get("/api/titles/suggest")
def api_suggest_titles(
    q: str = Query(""),
    level: str | None = Query(None, alias="org_level"),
    limit: int = Query(30, ge=1, le=200),
) -> dict[str, Any]:
    conn = connect(_db_path())
    try:
        items = suggest_titles(q, org_level=level, limit=limit, conn=conn)
    finally:
        conn.close()
    return {"items": items}


@app.get("/api/names/suggest")
def api_suggest_names(
    q: str = Query(""),
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    limit: int = Query(100, ge=1, le=300),
) -> dict[str, Any]:
    conn = connect(_db_path())
    try:
        items = suggest_names(
            q,
            org_level=level,
            bureau_code=bureau,
            limit=limit,
            conn=conn,
        )
    finally:
        conn.close()
    return {"items": items}


@app.get("/api/search")
def api_search(
    title: str | None = None,
    department: str | None = None,
    name: str | None = None,
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit: str | None = Query(None, description="总局单位代码，如 sta:press"),
    unit_category: str | None = Query(None, description="internal|direct|dispatched|municipality|province|autonomous"),
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """多维组合筛选；科室/职务/姓名留空时按地区浏览，全部留空则列出库内人员（分页）。"""
    conn = connect(_db_path())
    try:
        total, current_count, hits = search_people_page(
            title=title,
            department=department,
            name=name,
            org_level=level,
            bureau_code=bureau,
            unit_code=unit,
            unit_category=unit_category,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return {
        "total": total,
        "current_count": current_count,
        "offset": offset,
        "limit": limit,
        "items": [_slim_hit(h) for h in hits],
    }


@app.get("/api/departments/lookup")
def api_department_lookup(
    department: str | None = Query(None),
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit_category: str | None = Query(
        None, description="internal|direct|dispatched|municipality|province|autonomous"
    ),
    staff_limit: int = Query(30, ge=0, le=200),
    staff_offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """科室 → 分管领导 + 任职人员。科室可空=按地区浏览全体。"""
    conn = connect(_db_path(), light=True)
    try:
        result = lookup_department(
            department or "",
            org_level=level,
            bureau_code=bureau,
            unit_category=unit_category,
            staff_limit=staff_limit,
            staff_offset=staff_offset,
            conn=conn,
        )
    finally:
        conn.close()
    result["staff"] = [_slim_hit(s) for s in result.get("staff") or []]
    return result


@app.get("/api/departments/penetrate")
def api_department_penetrate(
    department: str | None = Query(None),
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit_category: str | None = Query(
        None, description="internal|direct|dispatched|municipality|province|autonomous"
    ),
    staff_limit: int = Query(50, ge=1, le=200),
    staff_offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """层级穿透：科室 → 分管领导 → 单位层级。科室可空=按地区浏览全体。"""
    conn = connect(_db_path(), light=True)
    try:
        result = penetrate_department(
            department or "",
            org_level=level,
            bureau_code=bureau,
            unit_category=unit_category,
            staff_limit=staff_limit,
            staff_offset=staff_offset,
            conn=conn,
        )
    finally:
        conn.close()
    result["staff"] = [_slim_hit(s) for s in result.get("staff") or []]
    return result


@app.get("/api/departments/{department}/leaders")
def api_department_leaders(
    department: str,
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    limit: int = Query(0, ge=0, le=2000, description="0 = all matches"),
) -> dict[str, Any]:
    conn = connect(_db_path(), light=True)
    try:
        items = leaders_for_department(
            department,
            org_level=level,
            bureau_code=bureau,
            limit=limit,
            conn=conn,
        )
    finally:
        conn.close()
    return {"department": department, "total": len(items), "items": items}


@app.get("/api/leaders/{name}/departments")
def api_leader_departments(
    name: str,
    bureau: str | None = Query(None, alias="bureau_code"),
) -> dict[str, Any]:
    conn = connect(_db_path())
    try:
        items = departments_for_leader(name, bureau_code=bureau, conn=conn)
    finally:
        conn.close()
    return {"name": name, "items": items}


@app.get("/api/people/{person_id:path}")
def api_person_profile(person_id: str) -> dict[str, Any]:
    """履历倒序 + 公告溯源链接。person_id 形如 shanghai:刘洪波"""
    conn = connect(_db_path())
    try:
        profile = get_person_profile(person_id, conn=conn)
    finally:
        conn.close()
    if profile is None:
        raise HTTPException(status_code=404, detail="person not found")
    # Ensure history is reverse-chronological (already ordered in store).
    return profile


@app.get("/api/export/search")
def api_export_search(
    title: str | None = None,
    department: str | None = None,
    name: str | None = None,
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit: str | None = Query(None, description="总局单位代码，如 sta:press"),
    unit_category: str | None = Query(None, description="internal|direct|dispatched|municipality|province|autonomous"),
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    fmt: Literal["csv", "xlsx"] = Query("csv"),
) -> Response:
    conn = connect(_db_path())
    try:
        hits = search_people(
            title=title,
            department=department,
            name=name,
            org_level=level,
            bureau_code=bureau,
            unit_code=unit,
            unit_category=unit_category,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            conn=conn,
        )
    finally:
        conn.close()
    data_rows = rows_from_search_hits(hits)
    filename = "search_export"
    if fmt == "xlsx":
        try:
            payload = to_xlsx_bytes(data_rows)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{filename}.xlsx"
    else:
        payload = to_csv_bytes(data_rows)
        media = "text/csv; charset=utf-8"
        filename = f"{filename}.csv"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }
    return Response(content=payload, media_type=media, headers=headers)


@app.get("/api/export/people/{person_id:path}")
def api_export_person(
    person_id: str,
    fmt: Literal["csv", "xlsx"] = Query("csv"),
) -> Response:
    conn = connect(_db_path())
    try:
        profile = get_person_profile(person_id, conn=conn)
    finally:
        conn.close()
    if profile is None:
        raise HTTPException(status_code=404, detail="person not found")
    data_rows = rows_from_profile(profile)
    safe_name = profile.get("name") or "person"
    if fmt == "xlsx":
        try:
            payload = to_xlsx_bytes(data_rows)
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"{safe_name}_履历.xlsx"
    else:
        payload = to_csv_bytes(data_rows)
        media = "text/csv; charset=utf-8"
        filename = f"{safe_name}_履历.csv"
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    }
    return Response(content=payload, media_type=media, headers=headers)


@app.get("/api/changes")
def api_changes(
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit_category: str | None = Query(
        None, description="internal|direct|dispatched|municipality|province|autonomous"
    ),
    change_type: str | None = Query(
        None,
        description="appoint|dismiss|transfer|promote|retire|probation_confirm|unknown",
    ),
    department: str | None = None,
    name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """全站变动动态流。"""
    conn = connect(_db_path())
    try:
        return list_changes(
            org_level=level,
            bureau_code=bureau,
            unit_category=unit_category,
            change_type=change_type,
            department=department,
            name=name,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            conn=conn,
        )
    finally:
        conn.close()


@app.get("/api/notices")
def api_notices(
    level: str | None = Query(None, alias="org_level"),
    bureau: str | None = Query(None, alias="bureau_code"),
    unit_category: str | None = Query(
        None, description="internal|direct|dispatched|municipality|province|autonomous"
    ),
    q: str | None = Query(None, description="标题 / 文号 / 发文机关关键词"),
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """最新人事任免公告（按任免日/发布日倒序）。"""
    conn = connect(_db_path(), light=True)
    try:
        return list_notices(
            org_level=level,
            bureau_code=bureau,
            unit_category=unit_category,
            q=q,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            conn=conn,
        )
    finally:
        conn.close()


@app.get("/api/posts/search")
def api_posts_search(
    department: str | None = None,
    title: str | None = None,
    bureau: str | None = Query(None, alias="bureau_code"),
    org_level: str | None = None,
    unit_category: str | None = Query(
        None, description="internal|direct|dispatched|municipality|province|autonomous"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """按科室 / 职务检索岗位列表（含现任、历任人数）。"""
    conn = connect(_db_path())
    try:
        return search_org_posts(
            department=department,
            title=title,
            bureau_code=bureau,
            org_level=org_level,
            unit_category=unit_category,
            limit=limit,
            offset=offset,
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/posts")
def api_posts(
    bureau: str = Query(..., alias="bureau_code"),
    department: str = Query(..., min_length=1),
    title: str | None = None,
) -> dict[str, Any]:
    """岗位现任 / 历任档案。"""
    conn = connect(_db_path())
    try:
        return post_archive(
            bureau_code=bureau,
            department=department,
            title=title,
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/anomalies/scan")
def api_anomalies_scan() -> dict[str, Any]:
    """扫描并刷新异常表。"""
    conn = connect(_db_path())
    try:
        return scan_anomalies(conn=conn)
    finally:
        conn.close()


@app.get("/api/anomalies")
def api_anomalies_list(
    status: str = Query("open", description="open|resolved|ignored|all"),
    kind: str | None = None,
    bureau: str | None = Query(None, alias="bureau_code"),
    severity: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = connect(_db_path())
    try:
        return list_anomalies(
            status=status,
            kind=kind,
            bureau_code=bureau,
            severity=severity,
            limit=limit,
            offset=offset,
            conn=conn,
        )
    finally:
        conn.close()


@app.post("/api/anomalies/{anomaly_id}/ignore")
def api_anomaly_ignore(
    anomaly_id: int,
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    note = (payload or {}).get("note")
    conn = connect(_db_path())
    try:
        return ignore_anomaly(anomaly_id, note=note, conn=conn)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/corrections")
def api_corrections_apply(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """人工修正回写。

    Body 示例::
        {
          "target_type": "appointment_event",
          "target_id": "123",
          "patch": {"person_name": "陈双格", "department_raw": "政策法规处"},
          "note": "修正命陈双格",
          "anomaly_id": 10
        }
    删除脏记录::
        {"target_type": "appointment_event", "target_id": "123", "delete": true, "note": "噪声"}
    """
    conn = connect(_db_path())
    try:
        return apply_correction(
            target_type=payload.get("target_type") or "",
            target_id=str(payload.get("target_id") or ""),
            patch=payload.get("patch") or {},
            note=payload.get("note"),
            anomaly_id=payload.get("anomaly_id"),
            delete=bool(payload.get("delete")),
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/corrections")
def api_corrections_list(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = connect(_db_path())
    try:
        return list_corrections(limit=limit, offset=offset, conn=conn)
    finally:
        conn.close()


# Account plane (PR10) — mounted before static so /login|/watches|/anomalies win.
from tax_platform.accounts import mount_accounts

mount_accounts(app)

# Static assets last so /api and HTML routes take precedence.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def create_app() -> FastAPI:
    return app
