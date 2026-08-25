from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite

SHANGHAI_CITY = BureauSite(
    code="shanghai",
    name="国家税务总局上海市税务局",
    level="province",
    parent_code="sta",
    home_url="https://shanghai.chinatax.gov.cn/",
    appointment_list_url="https://shanghai.chinatax.gov.cn/xxgk/rsxx/",
    leader_intro_url="https://shanghai.chinatax.gov.cn/xxgk/ldjj/",
    region="上海市",
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
        region="上海市",
    )


SHANGHAI_SITES: list[BureauSite] = [
    SHANGHAI_CITY,
    *[_district_site(code, name, path) for code, name, path in _DISTRICTS],
]
