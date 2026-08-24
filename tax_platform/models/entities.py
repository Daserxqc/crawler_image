from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class OrgLevel(str, Enum):
    HEADQUARTERS = "headquarters"  # 总局
    PROVINCE = "province"  # 省局
    CITY = "city"  # 市局
    DISTRICT = "district"  # 区县局


class ChangeType(str, Enum):
    APPOINT = "appoint"  # 新任职
    TRANSFER = "transfer"  # 调任
    PROMOTE = "promote"  # 晋升
    DISMISS = "dismiss"  # 免职
    RETIRE = "retire"  # 退休
    PROBATION_CONFIRM = "probation_confirm"  # 试用期满转正
    UNKNOWN = "unknown"


class DeptKind(str, Enum):
    """科室命名按层级区分，避免处/科混淆。"""

    SI = "si"  # 司
    CHU = "chu"  # 处（市局常见）
    KE = "ke"  # 科（区局常见）
    SUO = "suo"  # 税务所
    BRANCH = "branch"  # 分局
    OFFICE = "office"  # 办公室等综合机构
    OTHER = "other"


@dataclass
class NoticeMeta:
    """人事任免公告元数据。"""

    bureau_code: str
    title: str
    source_url: str
    published_at: datetime | None = None
    doc_no: str | None = None
    issuer: str | None = None
    issued_on: date | None = None
    raw_text: str = ""


@dataclass
class AppointmentEvent:
    """从任免公告中抽出的一条任/免记录。"""

    person_name: str
    action: str  # appoint | dismiss
    bureau_name: str | None = None
    department_raw: str | None = None
    title_raw: str | None = None
    probation_years: int | None = None
    effective_on: date | None = None
    source_url: str = ""
    notice_title: str = ""
    raw_clause: str = ""


@dataclass
class LeaderDuty:
    """领导介绍中的现任职务与分管科室。"""

    person_name: str
    gender: str | None = None
    ethnicity: str | None = None
    title_raw: str = ""
    duty_summary: str | None = None  # 主持全面工作 / 分管工作
    departments_raw: list[str] = field(default_factory=list)
    source_url: str = ""
    bureau_code: str = ""


@dataclass
class NormalizedTitle:
    raw: str
    canonical: str
    rank_hint: str | None = None  # 副司长级等，归一后可保留为附加属性


@dataclass
class NormalizedDepartment:
    raw: str
    canonical_name: str
    kind: DeptKind
    org_level: OrgLevel


@dataclass
class TenureRecord:
    """人员一次任职（履历一行）。"""

    person_name: str
    bureau_code: str
    department_canonical: str | None
    title_canonical: str
    change_type: ChangeType
    start_on: date | None
    end_on: date | None
    is_current: bool
    source_url: str
    notice_title: str | None = None


def to_dict(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "__dataclass_fields__"):
        data = asdict(obj)
        for key, value in list(data.items()):
            if isinstance(value, Enum):
                data[key] = value.value
            elif isinstance(value, (date, datetime)):
                data[key] = value.isoformat()
        return data
    raise TypeError(f"Unsupported type: {type(obj)!r}")
