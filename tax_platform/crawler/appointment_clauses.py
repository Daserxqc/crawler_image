"""Split appointment notice body into appoint/dismiss clauses."""

from __future__ import annotations

import re

from tax_platform.models.entities import AppointmentEvent, NoticeMeta
from tax_platform.normalize.person import SKIP_NAMES, is_plausible_person_name

NAME_RE = r"[\u4e00-\u9fa5·]{2,4}"
# Longer suffixes first so 「常务副局长」wins over 「副局长」.
TITLE_SUFFIXES = (
    "常务副局长",
    "副局长",
    "局长",
    "副司长",
    "司长",
    "副科长",
    "科长",
    "副所长",
    "所长",
    "副处长",
    "处长",
    "副主任",
    "主任",
    "副校长",
    "校长",
    "纪检组组长",
    "副组长",
    "组长",
    "党委书记",
    "党委委员",
    "总法律顾问",
    "总会计师",
    "总经济师",
    "总审计师",
    "一级巡视员",
    "二级巡视员",
    "巡视员",
    "一级高级主办",
    "二级高级主办",
    "三级高级主办",
    "四级高级主办",
    "一级主办",
    "二级主办",
    "三级主办",
    "四级主办",
    "高级主办",
    "主办",
    "一级调研员",
    "二级调研员",
    "三级调研员",
    "四级调研员",
    "调研员",
)
# Trailing concurrent ranks after顿号, e.g. 副局长、二级高级主办
_RANK_ONLY_RE = re.compile(
    r"^(?:[一二三四]级)?(?:高级)?(?:主办|调研员|巡视员)$"
)
# Optional leading 「任命 / 任命：」 so 「任命陈双格为…」 does not swallow 命 into the name.
# (?<![行]) avoids matching 「行为税」里的「为」误当成「X为Y」任命句式。
APPOINT_AS_RE = re.compile(
    rf"(?:任命[:：]?)*(?P<name>{NAME_RE})(?<![行])为(?P<post>[^；。;，,]+)"
)
# Shanghai-style: "赵健健任保税区税务分局法制科副科长"
APPOINT_RE = re.compile(rf"(?P<name>{NAME_RE})任(?P<post>[^；。;]+)")
DISMISS_RE = re.compile(rf"免去(?P<name>{NAME_RE})(?:的(?P<post>[^；。;]+?))?职务")
PROBATION_RE = re.compile(r"(?:任职)?试用期为?(?P<years>一|二|1|2)年")

_BODY_MARKERS = (
    "决定，任命",
    "决定:任命",
    "决定：任命",
    "研究决定，任命",
    "研究决定:任命",
    "研究决定：任命",
    "决定，免去",
    "决定：免去",
    "任命：",
    "任命:",
)


def extract_appointment_events(notice: NoticeMeta) -> list[AppointmentEvent]:
    events: list[AppointmentEvent] = []
    body = _focus_body(notice.raw_text)
    clauses = _split_clauses(body)
    for clause in clauses:
        events.extend(_events_from_clause(notice, clause))
    return _dedupe_events(events)


def _events_from_clause(notice: NoticeMeta, clause: str) -> list[AppointmentEvent]:
    out: list[AppointmentEvent] = []
    probation = _probation_years(clause)

    for dismiss in DISMISS_RE.finditer(clause):
        name = _clean_name(dismiss.group("name"))
        if not is_plausible_person_name(name):
            continue
        bureau, department, title = split_post(dismiss.group("post") or "")
        out.append(
            _event(
                notice,
                name=name,
                action="dismiss",
                bureau=bureau,
                department=department,
                title=title,
                clause=clause,
            )
        )

    for appoint_as in APPOINT_AS_RE.finditer(clause):
        name = _clean_name(appoint_as.group("name"))
        post = appoint_as.group("post")
        if not is_plausible_person_name(name):
            continue
        if post.startswith("任"):
            continue
        if not _looks_like_post(post):
            continue
        bureau, department, title = split_post(post)
        if not (title or department):
            continue
        out.append(
            _event(
                notice,
                name=name,
                action="appoint",
                bureau=bureau,
                department=department,
                title=title,
                clause=clause,
                probation_years=probation or _probation_years(clause[appoint_as.end() :]),
            )
        )

    # Only use 「X任Y」 when this clause had no 「X为Y」 hits (avoids double-count).
    if not out:
        for appoint in APPOINT_RE.finditer(clause):
            name = _clean_name(appoint.group("name"))
            post = appoint.group("post")
            if not is_plausible_person_name(name):
                continue
            if "免去" in clause[max(0, appoint.start() - 2) : appoint.start() + 2]:
                continue
            if not _looks_like_post(post):
                continue
            bureau, department, title = split_post(post)
            if not (title or department):
                continue
            out.append(
                _event(
                    notice,
                    name=name,
                    action="appoint",
                    bureau=bureau,
                    department=department,
                    title=title,
                    clause=clause,
                    probation_years=probation or _probation_years(clause[appoint.end() :]),
                )
            )
    return out


def split_post(post: str) -> tuple[str | None, str | None, str | None]:
    """Split '保税区税务分局法制科副科长' into unit / department / title."""
    text = re.sub(r"\s+", "", post)
    text = text.replace("职务", "").strip("的")
    text = PROBATION_RE.sub("", text).strip("，, 、")
    # Drop trailing rank / probation fragments left after imperfect splits.
    text = re.split(r"(?:，|,)?(?:任职)?试用期", text)[0].strip("，, ")
    # Drop rank notes like （副处长级）; keep （装备和采购处） by only stripping *级*.
    text = re.sub(r"[（(][^）)]*级[）)]", "", text)
    # Peel trailing concurrent ranks: 副局长、二级高级主办
    parts = [p for p in text.split("、") if p]
    ranks: list[str] = []
    while len(parts) > 1 and _RANK_ONLY_RE.fullmatch(parts[-1]):
        ranks.insert(0, parts.pop())
    text_for_title = "、".join(parts) if parts else text
    title = _match_suffix(text_for_title)
    if not title and ranks:
        title = ranks[0]
        ranks = ranks[1:]
        text_for_title = ""
    remainder = text_for_title[: -len(title)] if title else text_for_title
    if title and ranks:
        title = f"{title}、{'、'.join(ranks)}"
    # If parentheses still wrap a department alias, keep inner dept when useful.
    remainder = remainder.strip("（）() 、")
    bureau = None
    department = remainder or None
    for token in ("税务分局", "税务局", "干部学校", "稽查局"):
        index = remainder.rfind(token) if remainder else -1
        if index != -1:
            end = index + len(token)
            bureau = remainder[:end]
            department = remainder[end:] or None
            break
    # 总局本机关：国家税务总局人事司司长 → unit=总局, dept=人事司, title=司长
    if bureau is None and remainder and remainder.startswith("国家税务总局"):
        bureau = "国家税务总局"
        department = remainder[len("国家税务总局") :] or None
    if department:
        department = department.strip("（）() ，,、") or None
        # Reject department values that are clearly title/probation residue.
        if department and any(
            tok in department for tok in ("试用期", "任命", "免去", "通知")
        ):
            department = None
        # Pure rank fragments should not be kept as department.
        if department and _RANK_ONLY_RE.fullmatch(department):
            department = None
    return bureau, department, title


def _match_suffix(text: str) -> str | None:
    for suffix in TITLE_SUFFIXES:
        if text.endswith(suffix):
            return suffix
    return None


def _looks_like_post(post: str) -> bool:
    if not post:
        return False
    # 「任免…」「任职…」 false positives from titles / list pages.
    if post.startswith(("免", "职")):
        return False
    if post.startswith("用期"):
        return False
    if "通知" in post[:6]:
        return False
    return bool(
        _match_suffix(re.sub(r"[（(][^）)]*级[）)]", "", post))
        or any(
            tok in post
            for tok in (
                "税务",
                "处",
                "科",
                "所",
                "局",
                "办公室",
                "中心",
                "纪检",
                "党委",
                "分局",
            )
        )
    )


def _valid_name(name: str) -> bool:
    """Backward-compatible alias."""
    return is_plausible_person_name(name) and name not in SKIP_NAMES


def _clean_name(name: str) -> str:
    return re.sub(r"(同志)+$", "", (name or "").strip())


def _focus_body(text: str) -> str:
    """Drop nav/chrome before the appoint decision block when possible."""
    if not text:
        return ""
    best = -1
    for marker in _BODY_MARKERS:
        idx = text.find(marker)
        if idx != -1 and (best == -1 or idx < best):
            best = idx
    if best != -1:
        return text[best:]
    return text


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[。；;]", text)
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Keep "任职试用期为一年" attached to the preceding appoint clause.
        if merged and (part.startswith("任职试用期") or part.startswith("试用期")):
            merged[-1] = f"{merged[-1]}，{part}"
            continue
        merged.append(part)
    return merged


def _probation_years(clause: str) -> int | None:
    match = PROBATION_RE.search(clause)
    if not match:
        return None
    return 2 if match.group("years") in {"二", "2"} else 1


def _dedupe_events(events: list[AppointmentEvent]) -> list[AppointmentEvent]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[AppointmentEvent] = []
    for event in events:
        key = (
            event.person_name,
            event.action,
            event.title_raw or "",
            event.department_raw or "",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


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
        raw_clause=clause[:500],
    )
