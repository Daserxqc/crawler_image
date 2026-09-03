from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite

# subdomain → (code, 显示名, 省级行政区)
# 上海由 sites_shanghai.py 单独维护（含区局），此处跳过避免重复
PROVINCE_SUBDOMAINS: list[tuple[str, str, str]] = [
    ("beijing", "北京市", "beijing"),
    ("tianjin", "天津市", "tianjin"),
    ("hebei", "河北省", "hebei"),
    ("shanxi", "山西省", "shanxi"),
    ("neimenggu", "内蒙古自治区", "neimenggu"),
    ("liaoning", "辽宁省", "liaoning"),
    ("jilin", "吉林省", "jilin"),
    ("heilongjiang", "黑龙江省", "heilongjiang"),
    ("jiangsu", "江苏省", "jiangsu"),
    ("zhejiang", "浙江省", "zhejiang"),
    ("anhui", "安徽省", "anhui"),
    ("fujian", "福建省", "fujian"),
    ("jiangxi", "江西省", "jiangxi"),
    ("shandong", "山东省", "shandong"),
    ("henan", "河南省", "henan"),
    ("hubei", "湖北省", "hubei"),
    ("hunan", "湖南省", "hunan"),
    ("guangdong", "广东省", "guangdong"),
    ("guangxi", "广西壮族自治区", "guangxi"),
    ("hainan", "海南省", "hainan"),
    ("chongqing", "重庆市", "chongqing"),
    ("sichuan", "四川省", "sichuan"),
    ("guizhou", "贵州省", "guizhou"),
    ("yunnan", "云南省", "yunnan"),
    ("xizang", "西藏自治区", "xizang"),
    ("shaanxi", "陕西省", "shaanxi"),
    ("gansu", "甘肃省", "gansu"),
    ("qinghai", "青海省", "qinghai"),
    ("ningxia", "宁夏回族自治区", "ningxia"),
    ("xinjiang", "新疆维吾尔自治区", "xinjiang"),
]

# 部分站点使用短域名，后续探测失败时可在此覆盖
SUBDOMAIN_ALIASES: dict[str, str] = {
    "neimenggu": "nm",
    "shaanxi": "shaanxi",
}

# Hard-blocked / skip default xxgk probe (412/SSL/EOF): beijing, fujian, hunan, hebei
# 逐省探测得到的 URL 覆盖（scripts/discover_province_urls.py 生成后合并到此）
PROVINCE_URL_OVERRIDES: dict[str, dict[str, str]] = {
    "hebei": {
        "home_url": "http://hebei.chinatax.gov.cn/",
        "leader_intro_url": "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html",
        "appointment_list_url": "http://hebei.chinatax.gov.cn/hbswxxgk/gkml/1166/1247/1757/index.html"
    },
    "jilin": {
        "home_url": "https://jilin.chinatax.gov.cn/",
        "leader_intro_url": "https://jilin.chinatax.gov.cn/col/col24472/index.html",
        "appointment_list_url": "http://jilin.chinatax.gov.cn/col/col8211/index.html"
    },
    "jiangxi": {
        "home_url": "https://jiangxi.chinatax.gov.cn/",
        "leader_intro_url": "https://jiangxi.chinatax.gov.cn/col/col39358/index.html",
        "appointment_list_url": "https://jiangxi.chinatax.gov.cn/col/col32613/index.html"
    },
    "yunnan": {
        "home_url": "https://yunnan.chinatax.gov.cn/",
        "leader_intro_url": "https://yunnan.chinatax.gov.cn/col/col4701/index.html",
        "appointment_list_url": "https://yunnan.chinatax.gov.cn/col/col8641/index.html"
    },
    "xizang": {
        "home_url": "https://xizang.chinatax.gov.cn/",
        "leader_intro_url": "https://xizang.chinatax.gov.cn/col/col15212/index.html",
        "appointment_list_url": "https://xizang.chinatax.gov.cn/col/col15000/index.html"
    },
    "shaanxi": {
        "home_url": "https://shaanxi.chinatax.gov.cn/",
        "leader_intro_url": "https://shaanxi.chinatax.gov.cn/col/col36377/index.html",
        "appointment_list_url": "https://shaanxi.chinatax.gov.cn/col/col9016/index.html"
    },
    "gansu": {
        "home_url": "http://gansu.chinatax.gov.cn/",
        "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col3858/index.html",
        "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col129/index.html?number=A00009B00001A00016"
    },
    "beijing": {
        "home_url": "http://beijing.chinatax.gov.cn/",
        "leader_intro_url": "http://beijing.chinatax.gov.cn/bjswj/ldxx01/ldjianjie.shtml",
        "appointment_list_url": "http://beijing.chinatax.gov.cn/bjswj/c105858/cs_li.shtml"
    },
    "hunan": {
        "home_url": "https://hunan.chinatax.gov.cn/",
        "leader_intro_url": "https://hunan.chinatax.gov.cn/leaderlist/20181226000953",
        "appointment_list_url": "https://hunan.chinatax.gov.cn/lists/20190715095001"
    },
    "fujian": {
        "home_url": "https://fujian.chinatax.gov.cn/",
        "leader_intro_url": "https://fujian.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://fujian.chinatax.gov.cn/zfxxgkzl/zfxxgkml/zsjs/ryzl_838/"
    },
    "shandong": {
        "home_url": "https://shandong.chinatax.gov.cn/",
        "leader_intro_url": "https://shandong.chinatax.gov.cn/col/col10715/index.html",
        "appointment_list_url": "https://shandong.chinatax.gov.cn/col/col12/index.html?number=A20"
    },
    "chongqing": {
        "home_url": "https://chongqing.chinatax.gov.cn/cqtax/",
        "leader_intro_url": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/ldjj/",
        "appointment_list_url": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/rsxx/rsrm/"
    },
    "guangdong": {
        "home_url": "https://guangdong.chinatax.gov.cn/gdsw/index.shtml",
        "leader_intro_url": "https://guangdong.chinatax.gov.cn/gdsw/ldzl/leader.shtml",
        "appointment_list_url": "https://guangdong.chinatax.gov.cn/gdzdgkjbml/mlrsrm/zdgk_zfxxgk_list.shtml"
    },
    "jiangsu": {
        "leader_intro_url": "https://jiangsu.chinatax.gov.cn/art/2026/7/3/art_7642_1718741.html",
        "appointment_list_url": "https://jiangsu.chinatax.gov.cn/col/col21776/index.html"
    },
    "zhejiang": {
        "leader_intro_url": "https://zhejiang.chinatax.gov.cn/col/col10697/index.html",
        "appointment_list_url": "https://zhejiang.chinatax.gov.cn/col/col24799/index.html",
        "home_url": "https://zhejiang.chinatax.gov.cn/"
    },
    "neimenggu": {
        "leader_intro_url": "https://neimenggu.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://neimenggu.chinatax.gov.cn/xxgk/rsxx_25345/"
    },
    "hubei": {
        "appointment_list_url": "http://hubei.chinatax.gov.cn/hbsw/xxgk/rsrm/index.html",
        "home_url": "http://hubei.chinatax.gov.cn/",
        "leader_intro_url": "http://hubei.chinatax.gov.cn/hbsw/xxgk/ldjj/index.html"
    },
    "liaoning": {
        "home_url": "https://liaoning.chinatax.gov.cn/",
        "leader_intro_url": "https://liaoning.chinatax.gov.cn/col/col6555/index.html",
        "appointment_list_url": "https://liaoning.chinatax.gov.cn/col/col1214/index.html"
    },
    "henan": {
        "home_url": "https://henan.chinatax.gov.cn/",
        "leader_intro_url": "https://henan.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://henan.chinatax.gov.cn/xxgk/rsgl/rsrm/",
    },
    "guangxi": {
        "home_url": "https://guangxi.chinatax.gov.cn/",
        "leader_intro_url": "https://guangxi.chinatax.gov.cn/xxgk/ldjj/202508/t20250820_421700.html",
        "appointment_list_url": "https://guangxi.chinatax.gov.cn/xxgk/zfxxgk_22500/fdzdgknr/rsxx_22567/rsrm_22568/"
    },
    "guizhou": {
        "home_url": "https://guizhou.chinatax.gov.cn/",
        "leader_intro_url": "https://guizhou.chinatax.gov.cn/xxgk/ldjj/",
        "appointment_list_url": "https://guizhou.chinatax.gov.cn/xxgk/rsrm1/"
    },
    "anhui": {
        "leader_intro_url": "https://anhui.chinatax.gov.cn/col/col7889/index.html",
        "appointment_list_url": "https://anhui.chinatax.gov.cn/col/col22602/index.html"
    },
    "sichuan": {
        "home_url": "https://sichuan.chinatax.gov.cn/",
        "leader_intro_url": "https://sichuan.chinatax.gov.cn/col/col20404/index.html",
        "appointment_list_url": "https://sichuan.chinatax.gov.cn/col/col20006/index.html?number=A002001"
    },
    "ningxia": {
        "home_url": "https://ningxia.chinatax.gov.cn/",
        "leader_intro_url": "https://ningxia.chinatax.gov.cn/col/col14807/index.html",
        "appointment_list_url": "https://ningxia.chinatax.gov.cn/col/col10883/index.html"
    },
    "hainan": {
        "home_url": "https://hainan.chinatax.gov.cn/",
        "leader_intro_url": "https://hainan.chinatax.gov.cn/xxgk_1_13_1/",
        "appointment_list_url": "https://hainan.chinatax.gov.cn/xxgk_3/"
    },
    "xinjiang": {
        "home_url": "https://xinjiang.chinatax.gov.cn/",
        "leader_intro_url": "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/jggk/ldjj/202605/t20260521_157910.html",
        "appointment_list_url": "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/rsjy/rsrm_22397/"
    },
    "tianjin": {
        "home_url": "https://tianjin.chinatax.gov.cn/",
        "leader_intro_url": "https://tianjin.chinatax.gov.cn/u_zlmView.action?fjdm=11200000000&lmdm=010002",
        "appointment_list_url": "https://tianjin.chinatax.gov.cn/u_zlmViewMx.action?fjdm=11200000000&lmdm=01000401"
    },
    "heilongjiang": {
        "home_url": "https://heilongjiang.chinatax.gov.cn/",
        "leader_intro_url": "https://heilongjiang.chinatax.gov.cn/col/col11194/index.html",
        "appointment_list_url": "http://heilongjiang.chinatax.gov.cn/col/col17418/index.html"
    },
    "shanxi": {
        "home_url": "https://shanxi.chinatax.gov.cn/",
        "leader_intro_url": "https://shanxi.chinatax.gov.cn/xxgk/leader/sx-11400",
        "appointment_list_url": "http://shanxi.chinatax.gov.cn/son/list/sx-11400-4187"
    },
    "qinghai": {
        "home_url": "http://qinghai.chinatax.gov.cn/",
        "leader_intro_url": "http://qinghai.chinatax.gov.cn/web/sjtwz/ldjs.shtml",
        "appointment_list_url": "http://qinghai.chinatax.gov.cn/web/rmgg/xxgk_fdzd_list.shtml"
    }
}


def _province_site(code: str, region_name: str, subdomain: str) -> BureauSite:
    host = SUBDOMAIN_ALIASES.get(subdomain, subdomain)
    base = f"https://{host}.chinatax.gov.cn"
    overrides = PROVINCE_URL_OVERRIDES.get(code, {})
    return BureauSite(
        code=code,
        name=f"国家税务总局{region_name}税务局",
        level="province",
        parent_code="sta",
        home_url=overrides.get("home_url", f"{base}/"),
        appointment_list_url=overrides.get("appointment_list_url", f"{base}/xxgk/rsxx/"),
        leader_intro_url=overrides.get("leader_intro_url", f"{base}/xxgk/ldjj/"),
        region=region_name,
    )


PROVINCE_SITES: list[BureauSite] = [
    _province_site(code, region, subdomain) for code, region, subdomain in PROVINCE_SUBDOMAINS
]
