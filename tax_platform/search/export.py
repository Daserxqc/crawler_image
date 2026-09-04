"""Export search / profile rows to CSV or Excel."""

from __future__ import annotations

import csv
import io
from typing import Any


EXPORT_HEADERS = [
    "姓名",
    "单位代码",
    "层级",
    "角色",
    "现任职务",
    "现任科室",
    "任职起日",
    "任职止日",
    "任免类型",
    "职务",
    "科室",
    "公告标题",
    "来源链接",
]


def _event_date_key(ev: dict[str, Any]) -> str:
    return str(
        ev.get("started_on") or ev.get("effective_on") or ev.get("date") or ""
    )


def _event_recency_key(ev: dict[str, Any]) -> tuple:
    action = str(ev.get("change_type") or ev.get("action") or "").lower()
    # Same day transfer often emits dismiss+appoint; prefer appoint as "latest post".
    action_rank = 2 if action in {"appoint", "transfer", "promote"} else (
        1 if action in {"dismiss", "remove"} else 0
    )
    open_ended = 1 if not str(ev.get("ended_on") or "").strip() else 0
    return (_event_date_key(ev), open_ended, action_rank)


def _latest_event(events: list[Any]) -> dict[str, Any] | None:
    """Keep only the newest appointment/dismissal row for export."""
    dict_events = [e for e in events if isinstance(e, dict)]
    if not dict_events:
        return None
    return max(dict_events, key=_event_recency_key)


def rows_from_search_hits(hits: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for hit in hits:
        current = hit.get("current") or {}
        roles = "、".join(hit.get("roles") or [])
        profile = hit.get("profile") or {}
        history = profile.get("history") if isinstance(profile, dict) else None
        # Prefer enriched history (任职起/止); fall back to raw appointment rows.
        events = history if history else (hit.get("appointments") or [])
        ev = _latest_event(events) if events else None
        rows.append(
            [
                hit.get("name") or "",
                hit.get("bureau_code") or "",
                hit.get("org_level") or "",
                roles,
                str(current.get("title") or ""),
                str(current.get("department") or ""),
                _event_date_key(ev) if ev else "",
                str(ev.get("ended_on") or "") if ev else "",
                str(ev.get("change_type") or ev.get("action") or "") if ev else "",
                str(ev.get("title") or ev.get("title_raw") or "") if ev else "",
                str(ev.get("department") or ev.get("department_raw") or "") if ev else "",
                str(ev.get("notice_title") or "") if ev else "",
                str(ev.get("source_url") or "") if ev else "",
            ]
        )
    return rows


def rows_from_profile(profile: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    current = profile.get("current") or {}
    for ev in profile.get("history") or []:
        rows.append(
            [
                profile.get("name") or "",
                profile.get("bureau_code") or "",
                str(ev.get("level") or ""),
                "现任" if current.get("is_current") else "",
                str(current.get("title") or ""),
                str(current.get("department") or ""),
                str(ev.get("started_on") or ev.get("date") or ""),
                str(ev.get("ended_on") or ""),
                str(ev.get("change_type") or ""),
                str(ev.get("title") or ""),
                str(ev.get("department") or ""),
                str(ev.get("notice_title") or ""),
                str(ev.get("source_url") or ""),
            ]
        )
    return rows


def to_csv_bytes(data_rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_HEADERS)
    writer.writerows(data_rows)
    return buf.getvalue().encode("utf-8-sig")


def to_xlsx_bytes(data_rows: list[list[str]]) -> bytes:
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("openpyxl is required for Excel export") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "检索结果"
    ws.append(EXPORT_HEADERS)
    for row in data_rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
