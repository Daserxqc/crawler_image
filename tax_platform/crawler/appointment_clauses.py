"""Split appointment notice body into appoint/dismiss clauses."""

from __future__ import annotations

import re

from tax_platform.models.entities import AppointmentEvent, NoticeMeta

NAME_RE = r"[\u4e00-\u9fa5·]{2,4}"
TITLE_SUFFIXES = (
    "副科长",
    "科长",
    "副所长",
    "所长",
    "副处长",
    "处长",
    "副主任",
    "主任",
    "副局长",
    "局长",
    "副组长",
    "组长",
    "党委书记",
    "纪检组组长",
    "总会计师",
    "总经济师",
    "总审计师",
    "一级巡视员",
    "二级巡视员",
    "巡视员",
)
APPOINT_RE = re.compile(rf"(?P<name>{NAME_RE})任(?P<post>[^；。]+)")
DISMISS_RE = re.compile(rf"免去(?P<name>{NAME_RE})(?:的(?P<post>[^；。]+?))?职务")
PROBATION_RE = re.compile(r"任职试用期为(?P<years>一|二|1|2)年")


def extract_appointment_events(notice: NoticeMeta) -> list[AppointmentEvent]:
    events: list[AppointmentEvent] = []
    clauses = _split_clauses(notice.raw_text)
    for clause in clauses:
        probation = _probation_years(clause)
        dismiss = DISMISS_RE.search(clause)
        if dismiss:
            bureau, department, title = split_post(dismiss.group("post") or "")
            events.append(
                _event(
                    notice,
                    name=dismiss.group("name"),
                    action="dismiss",
                    bureau=bureau,
                    department=department,
                    title=title,
                    clause=clause,
                )
            )
            continue
        appoint = APPOINT_RE.search(clause)
        if appoint and "免去" not in clause[: appoint.start() + 2]:
            bureau, department, title = split_post(appoint.group("post"))
            events.append(
                _event(
                    notice,
                    name=appoint.group("name"),
                    action="appoint",
                    bureau=bureau,
                    department=department,
                    title=title,
                    clause=clause,
                    probation_years=probation or _probation_years(clause[appoint.end() :]),
                )
            )
    return events


def split_post(post: str) -> tuple[str | None, str | None, str | None]:
    """Split '保税区税务分局法制科副科长' into unit / department / title."""
    text = re.sub(r"\s+", "", post)
    text = text.replace("职务", "").strip("的")
    title = _match_suffix(text)
    remainder = text[: -len(title)] if title else text
    bureau = None
    department = remainder or None
    for token in ("税务分局", "税务局"):
        index = remainder.rfind(token) if remainder else -1
        if index != -1:
            end = index + len(token)
            bureau = remainder[:end]
            department = remainder[end:] or None
            break
    return bureau, department, title


def _match_suffix(text: str) -> str | None:
    for suffix in TITLE_SUFFIXES:
        if text.endswith(suffix):
            return suffix
    return None


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[。]", text)
    return [part.strip() for part in parts if part.strip()]


def _probation_years(clause: str) -> int | None:
    match = PROBATION_RE.search(clause)
    if not match:
        return None
    return 2 if match.group("years") in {"二", "2"} else 1


def _event(
    notice: NoticeMeta,
    *,
    name: str,
    action: str,
    bureau: str | None,
    department: str | None,
    title: str | None,
    clause: str,
    probation_years: int | None = None,
) -> AppointmentEvent:
    return AppointmentEvent(
        person_name=name,
        action=action,
        bureau_name=bureau,
        department_raw=department,
        title_raw=title,
        probation_years=probation_years,
        effective_on=notice.issued_on,
        source_url=notice.source_url,
        notice_title=notice.title,
        raw_clause=clause,
    )
