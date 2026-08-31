"""FastAPI routes for auth / watches / notify (mounted onto the public app)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from tax_platform.accounts import auth, notify, watches
from tax_platform.accounts.schema import connect
from tax_platform.store.schema import DEFAULT_DB_PATH

COOKIE_NAME = auth.COOKIE_NAME
DB_PATH: Path = Path(DEFAULT_DB_PATH)
STATIC_DIR: Path = Path(__file__).resolve().parents[1] / "web" / "static"

router = APIRouter(tags=["accounts"])


def _db():
    # Prefer the live web.app DB_PATH (tests patch it after import).
    try:
        from tax_platform.web import app as web_mod

        path = Path(getattr(web_mod, "DB_PATH", DB_PATH))
    except ImportError:
        path = Path(DB_PATH)
    return connect(path)


def _html(name: str) -> FileResponse:
    return FileResponse(
        STATIC_DIR / name,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


def _current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME)
    conn = _db()
    try:
        return auth.session_user(token, conn=conn)
    finally:
        conn.close()


def _require_user(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _set_session_cookie(response: Response, token: str, expires_at: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=auth.SESSION_DAYS * 86400,
        path="/",
    )


@router.get("/login")
def ui_login() -> FileResponse:
    return _html("login.html")


@router.get("/watches")
def ui_watches() -> RedirectResponse:
    return RedirectResponse(url="/account#watches", status_code=302)


@router.get("/account")
def ui_account() -> FileResponse:
    return _html("account.html")


@router.get("/anomalies", response_model=None)
def ui_anomalies(request: Request):
    """Admin-only surface: require login; not linked from public nav."""
    if _current_user(request) is None:
        return RedirectResponse(url="/login?next=/anomalies", status_code=302)
    return _html("anomalies.html")


@router.post("/api/auth/request-code")
def api_request_code(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    conn = _db()
    try:
        return auth.request_login_code(payload=payload, conn=conn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@router.post("/api/auth/verify")
def api_verify(response: Response, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    conn = _db()
    try:
        result = auth.verify_login_code(payload=payload, conn=conn)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    _set_session_cookie(response, result["token"], result["expires_at"])
    return {"ok": True, "user": result["user"], "expires_at": result["expires_at"]}


@router.get("/api/auth/me")
def api_me(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    if user is None:
        return {"authenticated": False, "user": None}
    return {"authenticated": True, "user": user}


@router.post("/api/auth/logout")
def api_logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    conn = _db()
    try:
        auth.logout(token, conn=conn)
    finally:
        conn.close()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.patch("/api/auth/profile")
def api_update_profile(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    return _update_profile(request, payload)


@router.post("/api/auth/profile")
def api_update_profile_post(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """POST alias for clients or proxies that block PATCH."""
    return _update_profile(request, payload)


def _update_profile(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        updated = auth.update_nickname(
            int(user["id"]),
            str(payload.get("nickname") or ""),
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return {"ok": True, "user": updated}


@router.get("/api/watches")
def api_list_watches(request: Request) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        return watches.list_watches(user["id"], conn=conn)
    finally:
        conn.close()


@router.post("/api/watches")
def api_add_watch(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        return watches.add_watch(
            user["id"],
            target_type=str(payload.get("target_type") or ""),
            target_id=str(payload.get("target_id") or ""),
            label=payload.get("label"),
            conn=conn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@router.delete("/api/watches/{watch_id}")
def api_delete_watch(watch_id: int, request: Request) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        return watches.remove_watch(user["id"], watch_id, conn=conn)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()


@router.get("/api/watches/check")
def api_watch_check(
    request: Request,
    target_type: str = Query(...),
    target_id: str = Query(...),
) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        row = watches.is_watching(
            user["id"],
            target_type=target_type,
            target_id=target_id,
            conn=conn,
        )
        return {"watching": row is not None, "watch": row}
    finally:
        conn.close()


@router.post("/api/notify/run")
def api_notify_run(
    request: Request,
    dry_run: bool = Query(True),
) -> dict[str, Any]:
    """Admin-ish: queue digests for the current user's watches (or all users in script)."""
    _require_user(request)
    conn = _db()
    try:
        return notify.run_notify_cycle(dry_run=dry_run, conn=conn)
    finally:
        conn.close()


@router.get("/api/notify/outbox")
def api_outbox(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    user = _require_user(request)
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT id, subject, status, created_at, sent_at, error, event_ids_json,
                   substr(body_text, 1, 320) AS body_preview
            FROM email_outbox
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user["id"], limit),
        ).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


def mount_accounts(app: FastAPI, *, db_path: Path | None = None) -> None:
    """Attach account routes to the public FastAPI app."""
    global DB_PATH
    if db_path is not None:
        DB_PATH = Path(db_path)
    app.include_router(router)
