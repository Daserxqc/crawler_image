"""Display helpers for search UI (short names, categories, title ranks)."""

from __future__ import annotations

import re

from tax_platform.config.sites import ALL_SITES, get_site, list_sites
from tax_platform.normalize.title import normalize_title

HQ_CATEGORIES = {
    "internal": "内设",
    "direct": "直属",
    "dispatched": "派出",
}

MACRO_CATEGORIES = {
    "headquarters": "总局",
    "municipality": "直辖市",
    "province": "省份",
    "autonomous": "自治区",
}

_DISPATCHED_HINTS = ("特派", "纪检组", "派出", "驻")
_DIRECT_HINTS = ("中心", "学院", "研究所", "培训", "党校", "杂志社", "报社", "出版社")


def short_bureau_name(name: str | None, *, parent_name: str | None = None) -> str:
    text = str(name or "").replace("国家税务总局", "").replace("税务局", "").strip()
    if parent_name:
        parent_short = short_bureau_name(parent_name)
        if parent_short and text.startswith(parent_short):
            text = text[len(parent_short) :].strip()
    return text or str(name or "—")


def sanitize_posting_unit(raw: str | None) -> str:
    """Keep only the tax-bureau identity from a noisy ``bureau_name`` / unit string.

    Parser debris often concatenates 科室+职务+免去… into bureau_name; UI must not
    show that as 任职单位.
    """
    text = re.sub(r"\s+", "", str(raw or "").strip())
    if not text:
        return ""
    text = re.split(r"(?:，|,|；|;|。)?(?:同时)?免去", text, maxsplit=1)[0]
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = text.strip("，,；;、 ")
    if not text:
        return ""

    from tax_platform.crawler.appointment_clauses import split_post

    bureau, _dept, _title = split_post(text)
    if bureau and re.search(r"(税务局|税务分局|稽查局)", bureau):
        return bureau

    m = re.search(r"^(.+?(?:税务分局|稽查局|税务局))", text)
    if m:
        return m.group(1)

    # Looks like title/dept mash without a bureau token — not a unit.
    if re.search(
        r"(副?局长|副?科长|副?处长|主任|主办|调研员|巡视员|职级)",
        text,
    ) and not re.search(r"(税务局|税务分局|稽查局)", text):
        return ""
    if len(text) > 40:
        return ""
    return text


def sanitize_department_label(raw: str | None) -> str:
    """Strip rank / 职级 debris glued onto department names."""
    text = re.sub(r"\s+", "", str(raw or "").strip())
    if not text:
        return ""
    text = re.split(r"(?:，|,|；|;|。)?(?:同时)?免去", text, maxsplit=1)[0]
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(
        r"(?:、)?(?:[一二三四五六七八九十百]+级)?(?:高级)?主办(?:职级)?$",
        "",
        text,
    )
    text = re.sub(r"职级$", "", text)
    return text.strip("，,；;、 ") or ""


def format_title_display(title_raw: str | None = None, title_canonical: str | None = None) -> str:
    raw = title_raw or title_canonical or ""
    nt = normalize_title(raw)
    if not nt:
        return title_canonical or title_raw or "—"
    base = nt.canonical or title_canonical or title_raw or "—"
    if nt.rank_hint:
        return f"{base}（{nt.rank_hint}）"
    return base


def _pick_title_raw(hit: dict) -> str | None:
    current = hit.get("current") or {}
    for row in hit.get("appointments") or []:
        if row.get("title_raw"):
            return row["title_raw"]
    profile = hit.get("profile") or {}
    for row in profile.get("history") or []:
        if row.get("title"):
            return row["title"]
    return current.get("title")


def _unit_is_sta_headquarters(unit: str, matched: str = "国家税务总局") -> bool:
    """True only when ``unit`` is STA itself or an STA 本机关/内设/直属 unit.

    Subordinate bureaus always start with 「国家税务总局…税务局」but are not HQ.
    """
    text = (unit or "").strip()
    if not text:
        return False
    if matched not in text and text != matched:
        # e.g. bare 「贵州省税务局」is never STA HQ.
        return False
    rest = text.split(matched, 1)[-1].strip(" （()）") if matched in text else text
    if not rest:
        return True
    # Local / regional tax bureaus under the STA prefix.
    if re.search(r"(税务局|税务分局|稽查局)", rest):
        return False
    if re.search(r"(省|市|州|盟|旗|区|县|自治区|自治州|生态城|新区|开发区)", rest):
        return False
    return True


def infer_org_level_from_unit(unit: str | None) -> str | None:
    """Infer org level from 任职单位 text when the site registry has no match."""
    text = str(unit or "").strip()
    if not text:
        return None
    if text == "国家税务总局" or _unit_is_sta_headquarters(text):
        return "headquarters"
    # 分局/税务所/稽查局：优先于文中嵌套的「××市税务局」片段
    if "税务分局" in text or "稽查局" in text or re.search(r"税务所(?:$|（|\()", text):
        return "district"
    if re.search(r"(省|自治区).{0,12}税务局", text) or "内蒙古自治区税务局" in text:
        return "province"
    if re.search(r"(市|自治州|州|盟).{0,12}税务局", text):
        return "city"
    if re.search(r"(区|县|旗|生态城|开发区|新区|保税).{0,12}税务局", text):
        return "district"
    # Unusual local names: 五家渠税务局 etc.
    if text.startswith("国家税务总局") and "税务局" in text:
        return "city"
    return None


def _primary_bureau_phrase(unit: str) -> str:
    """First 税务局/分局/稽查局 segment (ignore later 免去… clauses)."""
    text = (unit or "").strip()
    if not text:
        return ""
    # Cut at common clause separators before matching.
    head = re.split(r"[，,；;。]|免去|同时免去", text, maxsplit=1)[0]
    m = re.search(
        r"(?:国家税务总局)?[\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}?(?:税务分局|稽查局|税务局)",
        head,
    )
    return (m.group(0) if m else head).strip()


def posting_belongs_to_bureau(bureau_code: str, current: dict | None = None) -> bool:
    """True when任职单位 is 本级 of ``bureau_code``, not a subordinate other bureau.

    City notices often appoint county/分局 chiefs while ``persons.bureau_code`` stays
    the city. Selecting 保山市 should not list 腾冲市 / 隆阳区 / 施甸县 as 本级.
    City-owned 第N税务分局 under the city bureau name still count as 本级.
    """
    code = (bureau_code or "").strip()
    if not code:
        return True
    try:
        site = get_site(code)
    except KeyError:
        return True
    if site.level not in {"city", "district"}:
        return True

    current = current or {}
    unit = str(current.get("unit") or "").strip()
    if not unit:
        return True

    site_name = (site.name or "").strip()
    short = short_bureau_name(site_name)
    primary = _primary_bureau_phrase(unit) or unit

    if site_name and site_name in primary:
        return True

    if short and len(short) >= 2 and short in primary:
        rest = primary
        for prefix in ("国家税务总局", short):
            if prefix and prefix in rest:
                rest = rest.replace(prefix, "", 1)
        rest = rest.strip(" （()）")
        # 保山市 + 隆阳区税务局 → subordinate district bureau
        if re.match(r".{0,12}?(区|县|旗).{0,12}?税务", rest):
            return False
        # 保山市 + 第一税务分局 / 稽查局 / empty → 本级
        if (
            not rest
            or rest.startswith("第")
            or "税务分局" in rest
            or "稽查局" in rest
            or re.search(r"(科|处|室|所|中心)$", rest)
        ):
            return True
        if re.search(r"(税务局|税务分局|稽查局)", rest):
            return False
        return True

    matched = _match_site_from_unit(primary)
    if matched is not None:
        return matched.code == code

    if re.search(r"(税务局|税务分局|稽查局)", primary):
        return False
    return True


def bureau_needs_posting_scope(bureau_code: str | None) -> bool:
    """City/district concrete picks should scope hits to 本级 postings."""
    code = (bureau_code or "").strip()
    if not code:
        return False
    try:
        return get_site(code).level in {"city", "district"}
    except KeyError:
        return False


def _match_site_from_unit(unit: str):
    """Longest registry name/short-name match inside 任职单位 text."""
    best = None
    best_len = 0
    if not unit:
        return None
    for site in ALL_SITES:
        name = site.name or ""
        short = short_bureau_name(name)
        candidates = [name]
        if short:
            candidates.append(f"{short}税务局")
            candidates.append(short)
        for cand in candidates:
            if not cand or len(cand) < 2:
                continue
            if cand in unit and len(cand) > best_len:
                if site.code == "sta" and not _unit_is_sta_headquarters(unit, cand):
                    continue
                best = site
                best_len = len(cand)
    return best


def resolve_posting_site(bureau_code: str, current: dict | None = None):
    """Resolve the *actual* work unit site (not merely the appointing bureau).

    STA notices often appoint someone to 贵州省税务局 while events are stored under
    ``bureau_code=sta``. Sorting / level tags should follow the posting unit.
    """
    current = current or {}
    unit = str(current.get("unit") or "").strip()
    best = _match_site_from_unit(unit)
    if best is not None:
        return best
    try:
        site = get_site(bureau_code)
    except KeyError:
        return None
    # Do not treat STA-sourced appointments to local bureaus as headquarters.
    if site.code == "sta" and unit and not _unit_is_sta_headquarters(unit):
        return None
    return site


def _posting_org_level(bureau_code: str, current: dict | None = None) -> tuple[str | None, str | None]:
    """Return (org_level, posting_bureau_code) for display/sort."""
    current = current or {}
    unit = str(current.get("unit") or "").strip()
    inferred = infer_org_level_from_unit(unit)
    matched = _match_site_from_unit(unit)
    if matched is not None:
        if matched.code == "sta" and inferred and inferred != "headquarters":
            return inferred, None
        return matched.level, matched.code
    # No registry hit on unit text — prefer unit wording over appointing bureau.
    if inferred:
        return inferred, None
    try:
        site = get_site(bureau_code)
        return site.level, site.code
    except KeyError:
        return None, bureau_code or None


def infer_unit_category(bureau_code: str, current: dict | None = None) -> str:
    current = current or {}
    dept = str(current.get("department") or "")
    title = str(current.get("title") or "")
    unit = str(current.get("unit") or "")
    combined = dept + title + unit

    if any(h in combined for h in _DISPATCHED_HINTS):
        return "dispatched"

    level, code = _posting_org_level(bureau_code, current)
    site = resolve_posting_site(bureau_code, current)
    name = site.name if site is not None else unit

    if level == "headquarters" or code == "sta":
        if any(h in dept for h in _DIRECT_HINTS):
            return "direct"
        return "internal"

    if re.search(r"北京市|天津市|上海市|重庆市", name or ""):
        return "municipality"
    if "自治区" in (name or ""):
        return "autonomous"
    if level == "province":
        return "province"
    return ""


def bureau_codes_for_category(
    *,
    org_level: str | None = None,
    unit_category: str | None = None,
) -> list[str] | None:
    """Bureau codes matching region filters.

    Returns ``None`` when no category restriction applies.
    Empty list means no sites match.
    Includes city/district children under a matching province parent
    (e.g. 直辖市 → 上海区县).
    """
    cat = (unit_category or "").strip()
    if not cat:
        return None
    level_q = (org_level or "").strip() or None

    if cat in HQ_CATEGORIES:
        if level_q and level_q != "headquarters":
            return []
        return ["sta"]

    sites = list_sites()
    by_code = {s.code: s for s in sites}
    out: list[str] = []
    for site in sites:
        if level_q and site.level != level_q:
            continue
        if site.level == "headquarters":
            continue
        own = infer_unit_category(site.code)
        if own == cat:
            out.append(site.code)
            continue
        parent = by_code.get(site.parent_code or "")
        if parent and infer_unit_category(parent.code) == cat:
            out.append(site.code)
    return out


def unit_display(bureau_code: str, current: dict | None = None) -> str:
    current = current or {}
    unit = sanitize_posting_unit(current.get("unit"))
    if unit:
        return short_bureau_name(unit)
    site = resolve_posting_site(bureau_code, current)
    if site is not None:
        return short_bureau_name(site.name)
    try:
        return short_bureau_name(get_site(bureau_code).name)
    except KeyError:
        return bureau_code or "—"


def region_display(bureau_code: str, current: dict | None = None) -> str:
    current = current or {}
    dept = str(current.get("department") or "")
    unit = str(current.get("unit") or "")
    for text in (dept, unit):
        m = re.search(r"([\u4e00-\u9fff]{2,12}(?:省|市|自治区))", text)
        if m:
            return m.group(1)
    site = resolve_posting_site(bureau_code, current)
    if site is None:
        # resolve_posting_site may return None for ambiguous units (e.g. bare「稽查局」);
        # still show the appointing bureau's Chinese name — never leak raw codes like「sta」.
        try:
            site = get_site(bureau_code)
        except KeyError:
            return "—" if not bureau_code else bureau_code
    if site.level == "district" and site.parent_code:
        try:
            parent = get_site(site.parent_code)
            return short_bureau_name(parent.name)
        except KeyError:
            pass
    return short_bureau_name(site.name)


def category_label(cat: str) -> str:
    return HQ_CATEGORIES.get(cat) or MACRO_CATEGORIES.get(cat) or cat


def headquarters_org_bucket(hit: dict) -> int:
    """Within 总局: 本机关班子 → 内设司局 → 直属 → 派出.

    Lower number sorts first. Non-headquarters hits should not use this
    (callers pass 0).
    """
    cat = hit.get("unit_category") or ""
    current = hit.get("current") or {}
    dept = str(current.get("department") or "").strip()
    unit_disp = str(hit.get("unit_display") or "").strip()
    unit_raw = str(current.get("unit") or "").strip()
    unit = unit_disp or short_bureau_name(unit_raw) or unit_raw

    if cat == "dispatched" or any(h in (dept + unit + unit_raw) for h in _DISPATCHED_HINTS):
        return 3
    if cat == "direct" or any(h in (dept + unit) for h in _DIRECT_HINTS):
        return 2
    # 本机关领导班子：无具体科室，单位就是总局本身
    if not dept and unit in ("", "国家税务总局", "总局", "—"):
        return 0
    if not dept and (not unit_raw or unit_raw in ("国家税务总局", "总局")):
        return 0
    if cat == "internal" or dept:
        return 1
    return 0


def enrich_hit_display(hit: dict) -> dict:
    current = dict(hit.get("current") or {})
    cleaned_unit = sanitize_posting_unit(current.get("unit"))
    cleaned_dept = sanitize_department_label(current.get("department"))
    if cleaned_unit != (current.get("unit") or "") or cleaned_dept != (
        current.get("department") or ""
    ):
        current["unit"] = cleaned_unit or None
        if cleaned_dept:
            current["department"] = cleaned_dept
        hit["current"] = current
    title_raw = _pick_title_raw(hit)
    level, posting_code = _posting_org_level(hit.get("bureau_code") or "", current)
    hit["title_display"] = format_title_display(title_raw, current.get("title"))
    hit["unit_category"] = infer_unit_category(hit.get("bureau_code") or "", current)
    hit["unit_display"] = unit_display(hit.get("bureau_code") or "", current)
    hit["region_display"] = region_display(hit.get("bureau_code") or "", current)
    hit["category_label"] = category_label(hit["unit_category"]) if hit["unit_category"] else ""
    # Display / sort by where they actually work, not appointing bureau.
    if level:
        hit["org_level"] = level
    hit["posting_bureau_code"] = posting_code or hit.get("bureau_code")
    hit["unit_sort_key"] = hit.get("unit_display") or hit.get("region_display") or ""
    return hit


def headquarters_ranked_post(title_raw: str | None, department_raw: str | None) -> bool:
    text = f"{title_raw or ''}{department_raw or ''}"
    return any(h in text for h in _DISPATCHED_HINTS + ("司长", "副司长", "总局"))
