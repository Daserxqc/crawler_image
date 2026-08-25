from __future__ import annotations

from tax_platform.config.bureau_site import BureauSite

# 总局：任免与领导栏目路径与其他层级不同，单独配置
STA_HEADQUARTERS = BureauSite(
    code="sta",
    name="国家税务总局",
    level="headquarters",
    parent_code=None,
    home_url="https://www.chinatax.gov.cn/",
    appointment_list_url=(
        "https://www.chinatax.gov.cn/chinatax/n810214/c102374/c102384/n810611r/"
    ),
    leader_intro_url="https://www.chinatax.gov.cn/n810209/index.html",
    region="全国",
)
