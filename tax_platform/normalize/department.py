from __future__ import annotations

import re

from tax_platform.models.entities import DeptKind, NormalizedDepartment, OrgLevel

DEPT_SUFFIX_KIND: list[tuple[str, DeptKind]] = [
    ("税务分局", DeptKind.BRANCH),
    ("稽查局", DeptKind.BRANCH),
    ("风险管理局", DeptKind.BRANCH),
    ("税务所", DeptKind.SUO),
    ("办公室", DeptKind.OFFICE),
    ("办公厅", DeptKind.OFFICE),
    ("处", DeptKind.CHU),
    ("科", DeptKind.KE),
    ("司", DeptKind.SI),
    ("中心", DeptKind.OTHER),
    ("局", DeptKind.OTHER),
]

# Truncated fragments commonly glued/cut in appointment text → preferred full names.
FRAGMENT_EXPAND: dict[str, str] = {
    "货物": "货物和劳务税",
    "财产": "财产和行为税",
    "社保": "社会保险费",
    "社会保险费": "社会保险费",
    "收入核算": "收入核算",
    "征管": "征收管理",
    "收科": "税收科",
}

# Noise tokens that are not department names.
DEPT_NOISE = {
    "全面工作",
    "主持全面工作",
    "分管工作",
    "负责全面工作",
    "党委委员",
    "国家税务总局",
}

# Rank / title fragments that must never become departments (even with 处/科 glued on).
DEPT_TITLE_NOISE_RE = re.compile(
    r"(试用期|主办|调研员|巡视员|总会计师|总经济师|总审计师|"
    r"书记|局长|处长|科长|所长|主任|任职|免职|转正)"
)


TITLE_DEPT_RE = re.compile(
    r"(?P<dept>[\u4e00-\u9fa5·（）()]{2,30}?(?:处|科|司|所|分局|办公室|办公厅|中心|局))"
    r"(?:副?(?:处|科|司|所)?长|主任|局长|专员|组长)?"
)


def split_department_raw(raw: str | None) -> list[str]:
    """Split a glued department field into candidate department names."""
    if not raw:
        return []
    text = re.sub(r"\s+", "", raw)
    text = re.split(r"联系单位|负责全面|分管工作|主持", text)[0]
    parts = re.split(r"[、，,；;。/｜|]", text)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = clean_department_name(part)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def clean_department_name(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"\s+", "", raw).strip("、，,；;。的：:")
    text = re.sub(r"^国家税务总局", "", text)
    text = text.strip("（）()【】[]")
    if not text or text in DEPT_NOISE:
        return None
    # Drop clause fragments mistaken as department names.
    if any(tok in text for tok in ("任命", "免去", "同志", "欢迎", "经研究", "决定", "请大家")):
        return None
    if DEPT_TITLE_NOISE_RE.search(text):
        return None
    if len(text) < 2 or len(text) > 24:
        return None
    # Must look like an org unit, not a bare stem we'll invent later for ranks.
    has_unit = any(
        s in text for s in ("处", "科", "司", "所", "局", "办公室", "办公厅", "中心", "分局")
    )
    # Expand truncated stems when they look incomplete (no 处/科/司/所).
    if not has_unit:
        expanded = False
        for stem, full in FRAGMENT_EXPAND.items():
            if text == stem or text.startswith(stem):
                text = full
                expanded = True
                break
        if not expanded:
            return None
    return text or None


def department_from_title(title: str | None) -> str | None:
    """Extract embedded department from titles like 政策法规处副处长."""
    if not title:
        return None
    text = re.sub(r"\s+", "", title)
    match = TITLE_DEPT_RE.search(text)
    if not match:
        return None
    return clean_department_name(match.group("dept"))


def normalize_department(
    raw: str | None,
    *,
    org_level: OrgLevel = OrgLevel.DISTRICT,
) -> NormalizedDepartment | None:
    cleaned = clean_department_name(raw)
    if not cleaned:
        return None
    # Prefer level-typical suffix when fragment was expanded without suffix.
    display = cleaned
    if not any(s in display for s in ("处", "科", "司", "所", "局", "办公室", "中心", "分局")):
        if org_level in {OrgLevel.HEADQUARTERS, OrgLevel.PROVINCE, OrgLevel.CITY}:
            display = f"{display}处"
        elif org_level == OrgLevel.DISTRICT:
            display = f"{display}科"
    kind = _detect_kind(display, org_level)
    return NormalizedDepartment(
        raw=raw or cleaned,
        canonical_name=display,
        kind=kind,
        org_level=org_level,
    )


def org_level_for_bureau(level: str) -> OrgLevel:
    mapping = {
        "headquarters": OrgLevel.HEADQUARTERS,
        "province": OrgLevel.PROVINCE,
        "city": OrgLevel.CITY,
        "district": OrgLevel.DISTRICT,
    }
    return mapping.get(level, OrgLevel.DISTRICT)


def _detect_kind(text: str, org_level: OrgLevel) -> DeptKind:
    for suffix, kind in DEPT_SUFFIX_KIND:
        if text.endswith(suffix) or suffix in text:
            return kind
    if org_level == OrgLevel.HEADQUARTERS:
        return DeptKind.SI
    if org_level in {OrgLevel.PROVINCE, OrgLevel.CITY}:
        return DeptKind.CHU
    if org_level == OrgLevel.DISTRICT:
        return DeptKind.KE
    return DeptKind.OTHER
