"""国家税务总局机关司局与直属事业单位（供 UI 地区下拉）。"""

from __future__ import annotations

# 内设司局 / 总局机关
STA_INTERNAL_UNITS: list[tuple[str, str]] = [
    ("sta", "国家税务总局"),
    ("sta:office", "办公厅"),
    ("sta:policy", "政策法规司"),
    ("sta:goods_tax", "货物和劳务税司"),
    ("sta:income_tax", "所得税司"),
    ("sta:property_tax", "财产和行为税司"),
    ("sta:intl_tax", "国际税务司"),
    ("sta:social_insurance", "社会保险费司"),
    ("sta:planning", "规划核算司"),
    ("sta:service", "纳税服务司"),
    ("sta:tech", "征管和科技发展司"),
    ("sta:large_enterprise", "大企业税收管理司"),
    ("sta:supervision", "督察内审司"),
    ("sta:party", "党建工作局"),
    ("sta:personnel", "人事司"),
    ("sta:retired", "离退休干部局"),
]

# 直属事业单位
STA_DIRECT_UNITS: list[tuple[str, str]] = [
    ("sta:press", "中国税务出版社"),
    ("sta:news", "中国税务报社"),
    ("sta:magazine", "中国税务杂志社"),
    ("sta:academy", "税务干部学院"),
    ("sta:yangzhou", "国家税务总局扬州税务进修学院"),
    ("sta:data", "国家税务总局税收大数据和风险管理局"),
    ("sta:procurement", "国家税务总局集中采购中心"),
]

# 派出/派驻机构（按驻地分）
STA_DISPATCHED_UNITS: list[tuple[str, str]] = [
    ("sta:dispatched", "派出机构（全部）"),
]

UNIT_CATEGORY: dict[str, str] = {}
for code, _ in STA_INTERNAL_UNITS:
    UNIT_CATEGORY[code] = "internal"
for code, _ in STA_DIRECT_UNITS:
    UNIT_CATEGORY[code] = "direct"
for code, _ in STA_DISPATCHED_UNITS:
    UNIT_CATEGORY[code] = "dispatched"

ALL_STA_UNITS: list[tuple[str, str]] = [
    *STA_INTERNAL_UNITS,
    *STA_DIRECT_UNITS,
    *STA_DISPATCHED_UNITS,
]


def list_sta_units(category: str | None = None) -> list[dict[str, str]]:
    items = ALL_STA_UNITS
    if category:
        items = [(c, n) for c, n in items if UNIT_CATEGORY.get(c) == category]
    return [{"code": code, "name": name, "category": UNIT_CATEGORY.get(code, "")} for code, name in items]


def unit_keywords(code: str) -> list[str]:
    """Match tokens for search filtering."""
    mapping: dict[str, list[str]] = {
        "sta": ["国家税务总局"],
        "sta:press": ["税务出版社", "中国税务出版社"],
        "sta:news": ["税务报社", "中国税务报社"],
        "sta:magazine": ["税务杂志社", "中国税务杂志社"],
        "sta:academy": ["税务干部学院", "税院"],
        "sta:yangzhou": ["扬州", "进修学院"],
        "sta:data": ["税收大数据", "风险管理局"],
        "sta:procurement": ["集中采购"],
        "sta:party": ["党建工作局"],
        "sta:office": ["办公厅"],
        "sta:policy": ["政策法规"],
        "sta:dispatched": ["特派", "纪检组", "驻", "派出"],
    }
    if code in mapping:
        return mapping[code]
    for c, name in ALL_STA_UNITS:
        if c == code:
            return [name.replace("国家税务总局", "").strip() or name]
    return []
