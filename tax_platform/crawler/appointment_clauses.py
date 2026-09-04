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
# (?<![行无以]) avoids 「行为税 / 无为市 / 以为」里的「为」误当成任命句式。
# Optional 「同志」 must be outside the name group — otherwise
# 「任命周立渊同志为…」greedy-matches name=「立渊同志」→ cleaned「立渊」。
APPOINT_AS_RE = re.compile(
    rf"(?:(?:任命|聘任)[:：]?)?(?P<name>{NAME_RE})(?:同志)?(?<![行无以])为(?P<post>[^；。;，,]+)"
)
# 「翟盼正式任用为…」「赵湘任用为…」(prefer longer 「正式任用」 first)
APPOINT_ZHENGSHI_RENYONG_RE = re.compile(
    rf"(?P<name>{NAME_RE})正式任用为(?P<post>[^；。;，,]+)"
)
APPOINT_RENYONG_RE = re.compile(
    rf"(?P<name>{NAME_RE})任用为(?P<post>[^；。;，,]+)"
)
# 「张帆挂职任…副局长」「梁芳同志担任…副组长」
APPOINT_GUAZHI_RE = re.compile(
    rf"(?P<name>{NAME_RE})挂职任(?P<post>[^；。;，,]+)"
)
APPOINT_TONGZHI_DANREN_RE = re.compile(
    rf"(?:任命[:：]?)?(?P<name>{NAME_RE})同志担任(?P<post>[^；。;，,]+)"
)
# Shanghai-style: "赵健健任保税区税务分局法制科副科长"
# Do NOT use for 担任/兼任/聘任 — those are handled above / as dismiss.
APPOINT_RE = re.compile(rf"(?P<name>{NAME_RE})任(?P<post>[^；。;]+)")
# Fujian-style probation confirmation: "陈瑶任职试用期满，考核合格，按期转正，任…副局长"
CONFIRM_APPOINT_RE = re.compile(
    rf"(?P<name>{NAME_RE})任职试用期满[^。；;]{{0,40}}按期转正，任(?P<post>[^；。;，,]+)"
)
DISMISS_TONGZHI_RE = re.compile(
    rf"免去(?P<name>{NAME_RE})同志(?:的)?(?P<post>[^；。;]+?)(?:职务|职级)"
)
DISMISS_DE_RE = re.compile(rf"免去(?P<name>{NAME_RE})的(?P<post>[^；。;]+?)(?:职务|职级)")
DISMISS_BARE_RE = re.compile(rf"免去(?P<name>{NAME_RE})(?:职务|职级)")
# Liaoning / many cities: 「免去许绍华国家税务总局…副局长职务」(no 的/同志)
# Require post to start with 国家税务总局 so greedy {2,4} name won't swallow 「国」.
DISMISS_GLUED_RE = re.compile(
    rf"免去(?P<name>{NAME_RE})(?P<post>国家税务总局[^；。;]+?)(?:职务|职级)"
)
# Xinjiang / Changji-style: "常旭东不再担任…副局长"
DISMISS_NO_LONGER_RE = re.compile(
    rf"(?P<name>{NAME_RE})不再(?:担任|兼任)(?P<post>[^；。;]+)"
)
# 「不再聘任为…助理工程师」
DISMISS_NO_LONGER_PIN_RE = re.compile(
    rf"(?:(?P<name>{NAME_RE}))?不再聘任(?:为)?(?P<post>[^；。;]+)"
)
PROBATION_RE = re.compile(r"(?:任职)?试用期为?(?P<years>一|二|1|2)年")
# Chongqing / roster notices without 任命 verb (CJK spaces already collapsed):
# 「肖锋国家税务总局重庆市南岸区税务局办公室主任（…）陈光才国家税务总局…」
_TITLE_ALT = "|".join(TITLE_SUFFIXES)
ROSTER_APPOINT_RE = re.compile(
    rf"(?P<name>{NAME_RE})(?P<post>国家税务总局[\u4e00-\u9fa5、]{{2,80}}?(?:{_TITLE_ALT}))"
    rf"(?=(?:[（(]|[\u4e00-\u9fa5]{{2,4}}国家税务总局|$))"
)

_BODY_MARKERS = (
    "决定，任命",
    "决定:任命",
    "决定：任命",
    "决定，任命：",
    "决定：任命：",
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
    body = _focus_body(_normalize_cjk_spaces(notice.raw_text or ""))
    clauses = _split_clauses(body)
    for clause in clauses:
        events.extend(_events_from_clause(notice, clause))
    if not events:
        events.extend(_events_from_roster(notice, body))
    return _dedupe_events(events)


def _events_from_roster(notice: NoticeMeta, body: str) -> list[AppointmentEvent]:
    """Parse verb-less roster lines: 「姓名 国家税务总局…科长」."""
    out: list[AppointmentEvent] = []
    for match in ROSTER_APPOINT_RE.finditer(body):
        # 「免去许绍华国家税务总局…」— 贪心姓名会吃成「去许绍华」
        if match.start() > 0 and body[match.start() - 1] == "免":
            continue
        name = _clean_name(match.group("name"))
        post = match.group("post")
        if not is_plausible_person_name(name):
            continue
        if not _looks_like_post(post):
            continue
        bureau, department, title = split_post(post)
        if not (title or department):
            continue
        tail = body[match.end() : match.end() + 24]
        out.append(
            _event(
                notice,
                name=name,
                action="appoint",
                bureau=bureau,
                department=department,
                title=title,
                clause=match.group(0),
                probation_years=_probation_years(tail),
            )
        )
    return out


def _normalize_cjk_spaces(text: str) -> str:
    """Collapse soft line-breaks inside CJK runs (e.g. 「免 去」「纪 检组」)."""
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"([\u4e00-\u9fa5])\s+([\u4e00-\u9fa5])", r"\1\2", text)
    return text


def _events_from_clause(notice: NoticeMeta, clause: str) -> list[AppointmentEvent]:
    out: list[AppointmentEvent] = []
    probation = _probation_years(clause)

    for dismiss in _iter_dismiss_matches(clause):
        raw_name = dismiss.groupdict().get("name") or ""
        name = _clean_name(raw_name)
        if not is_plausible_person_name(name):
            continue
        post = dismiss.groupdict().get("post") or ""
        bureau, department, title = split_post(post)
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

    for pattern in (
        APPOINT_ZHENGSHI_RENYONG_RE,
        APPOINT_RENYONG_RE,
        APPOINT_GUAZHI_RE,
        APPOINT_TONGZHI_DANREN_RE,
        APPOINT_AS_RE,
    ):
        for appoint_as in pattern.finditer(clause):
            name = _clean_name(appoint_as.group("name"))
            post = appoint_as.group("post")
            if not is_plausible_person_name(name):
                continue
            if post.startswith("任") and pattern is APPOINT_AS_RE:
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
                    probation_years=probation
                    or _probation_years(clause[appoint_as.end() :]),
                )
            )

    # Only use 「X任Y」 when this clause had no 「X为Y」 hits (avoids double-count).
    for confirm in CONFIRM_APPOINT_RE.finditer(clause):
        name = _clean_name(confirm.group("name"))
        post = confirm.group("post")
        if not is_plausible_person_name(name):
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
                probation_years=probation,
            )
        )

    if not any(e.action == "appoint" for e in out):
        for appoint in APPOINT_RE.finditer(clause):
            name = _clean_name(appoint.group("name"))
            post = appoint.group("post")
            if not is_plausible_person_name(name):
                continue
            if "免去" in clause[max(0, appoint.start() - 2) : appoint.start() + 2]:
                continue
            # 「担任/兼任/聘任」— the matched 「任」 is not the appoint verb.
            ren_at = appoint.start() + len(appoint.group("name"))
            if ren_at > 0 and clause[ren_at - 1] in "担兼聘":
                continue
            if post.startswith("职试用期"):
                continue
            # 文号「沪税宝任〔2026〕2号」— 「任」后紧跟书名号，不是任命动词。
            if post.startswith(("〔", "﹝", "[", "【")):
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


def _iter_dismiss_matches(clause: str):
    seen_spans: set[tuple[int, int]] = set()
    for pattern in (
        DISMISS_NO_LONGER_RE,
        DISMISS_NO_LONGER_PIN_RE,
        DISMISS_TONGZHI_RE,
        DISMISS_DE_RE,
        DISMISS_BARE_RE,
        DISMISS_GLUED_RE,
    ):
        for match in pattern.finditer(clause):
            span = match.span()
            if span in seen_spans:
                continue
            seen_spans.add(span)
            yield match


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
    # 文号碎片：〔2026〕2号发文单位：…
    if post.startswith(("〔", "﹝", "[", "【")):
        return False
    if re.match(r"^\d{4}[〕﹞\]]", post):
        return False
    if "通知" in post[:6]:
        return False
    if "发文单位" in post[:20] or "索引号" in post[:30]:
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
    text = re.sub(r"(同志)+$", "", (name or "").strip())
    # Debris from 「免去姓名…」when 「免」was stripped / roster ate 「去」
    text = re.sub(r"^(?:免去|免)", "", text)
    if text.startswith("去") and len(text) >= 3:
        text = text[1:]
    return text


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
