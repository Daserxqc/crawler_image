from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BureauSite:
    """One tax bureau data source (city or district)."""

    code: str
    name: str
    level: str  # city | district
    parent_code: str | None
    home_url: str
    appointment_list_url: str
    leader_intro_url: str


SHANGHAI_CITY = BureauSite(
    code="shanghai",
    name="国家税务总局上海市税务局",
    level="city",
    parent_code=None,
    home_url="https://shanghai.chinatax.gov.cn/",
    # 市局公开栏目用「人事信息」聚合页，内含任免列表
    appointment_list_url="https://shanghai.chinatax.gov.cn/xxgk/rsxx/",
    leader_intro_url="https://shanghai.chinatax.gov.cn/xxgk/ldjj/",
)


_DISTRICTS: list[tuple[str, str, str]] = [
    ("pdtax", "浦东新区税务局", "pdtax"),
    ("hptax", "黄浦区税务局", "hptax"),
    ("xhtax", "徐汇区税务局", "xhtax"),
    ("jatax", "静安区税务局", "jatax"),
    ("cntax", "长宁区税务局", "cntax"),
    ("pttax", "普陀区税务局", "pttax"),
    ("hktax", "虹口区税务局", "hktax"),
    ("yptax", "杨浦区税务局", "yptax"),
    ("bstax", "宝山区税务局", "bstax"),
    ("mhtax", "闵行区税务局", "mhtax"),
    ("jdtax", "嘉定区税务局", "jdtax"),
    ("jstax", "金山区税务局", "jstax"),
    ("sjtax", "松江区税务局", "sjtax"),
    ("qptax", "青浦区税务局", "qptax"),
    ("fxtax", "奉贤区税务局", "fxtax"),
    ("cmtax", "崇明区税务局", "cmtax"),
]


def _district_site(code: str, short_name: str, path: str) -> BureauSite:
    base = f"https://shanghai.chinatax.gov.cn/{path}"
    return BureauSite(
        code=code,
        name=f"国家税务总局上海市{short_name}",
        level="district",
        parent_code="shanghai",
        home_url=f"{base}/",
        appointment_list_url=f"{base}/xxgk/rsrm/",
        leader_intro_url=f"{base}/xxgk/ldjj/",
    )


SHANGHAI_SITES: list[BureauSite] = [
    SHANGHAI_CITY,
    *[_district_site(code, name, path) for code, name, path in _DISTRICTS],
]


def get_site(code: str) -> BureauSite:
    for site in SHANGHAI_SITES:
        if site.code == code:
            return site
    raise KeyError(f"Unknown bureau site code: {code}")


def list_sites(level: str | None = None) -> list[BureauSite]:
    if level is None:
        return list(SHANGHAI_SITES)
    return [site for site in SHANGHAI_SITES if site.level == level]
