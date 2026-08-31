from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite

SHANGHAI_CITY = BureauSite(
    code="shanghai",
    name="国家税务总局上海市税务局",
    level="province",
    parent_code="sta",
    home_url="https://shanghai.chinatax.gov.cn/",
    appointment_list_url="https://shanghai.chinatax.gov.cn/xxgk/rsxx/jgrs/",
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

# 市局直属：稽查局 / 税务分局（路径取自官网子站）
# (code, short_name, path, appt_list_path, leader_intro_url)
_SPECIALS: list[tuple[str, str, str, str, str]] = [
    (
        "dyjcj",
        "第一稽查局",
        "dyjcj",
        "dyjcj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/dyjcj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjj/202408/t473047.html",
    ),
    (
        "dejcj",
        "第二稽查局",
        "dejcj",
        "dejcj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/dejcj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjj_1/202408/t473141.html",
    ),
    (
        "dsjcj",
        "第三稽查局",
        "dsjcj",
        "dsjcj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/dsjcj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjj/202408/t473208.html",
    ),
    (
        "dsijcj",
        "第四稽查局",
        "dsijcj",
        "dsijcj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/dsijcj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjj/202408/t473211.html",
    ),
    (
        "dwjcj",
        "第五稽查局",
        "dwjcj",
        # 该子站栏目路径为 ztlf（非 ztfl）
        "dwjcj/xxgk/zfxxgk/zfxxgkml/ztlf/rsrm/",
        "https://shanghai.chinatax.gov.cn/dwjcj/xxgk/zfxxgk/zfxxgkml/ztlf/jggk/ldjj/202604/t480129.html",
    ),
    (
        "swfj",
        "第三税务分局",
        "swfj",
        "swfj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/swfj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjjs/202306/t467558.html",
    ),
    (
        "sswfj",
        "第四税务分局",
        "sswfj",
        "sswfj/xxgk/zfxxgk/zfxxgkml/ztfl/rsrm/",
        "https://shanghai.chinatax.gov.cn/sswfj/xxgk/zfxxgk/zfxxgkml/ztfl/jggk/ldjj/202407/t472785.html",
    ),
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


def _special_site(
    code: str, short_name: str, path: str, appt_path: str, leader_url: str
) -> BureauSite:
    base = f"https://shanghai.chinatax.gov.cn/{path}"
    return BureauSite(
        code=code,
        name=f"国家税务总局上海市税务局{short_name}",
        level="district",
        parent_code="shanghai",
        home_url=f"{base}/",
        appointment_list_url=f"https://shanghai.chinatax.gov.cn/{appt_path}",
        leader_intro_url=leader_url,
        region="上海市",
    )


SHANGHAI_SITES: list[BureauSite] = [
    SHANGHAI_CITY,
    *[_district_site(code, name, path) for code, name, path in _DISTRICTS],
    *[
        _special_site(code, name, path, appt, leader)
        for code, name, path, appt, leader in _SPECIALS
    ],
]
