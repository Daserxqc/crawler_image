"""Parse tax-bureau leader introduction pages."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from tax_platform.crawler.http_client import (
    extract_js_redirect_url,
    extract_meta_refresh_url,
    resolve_list_child_url,
)
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name

# 2–4汉字, or spaced forms, or minority names with ·
NAME = (
    r"(?:[\u4e00-\u9fa5]{1,4}(?:·[\u4e00-\u9fa5]{1,4})+"
    r"|[\u4e00-\u9fa5](?:[\u3000\s\u2002\u2003]+[\u4e00-\u9fa5]){1,3}"
    r"|[\u4e00-\u9fa5]{2,4})"
)
# Yunnan city hubs list many bios without "。" between them:
# 「鲁维荣，男，彝族，中共党员，…局长 ‍ 周庆云，男，…」
PROFILE_RE = re.compile(
    rf"(?:^|[^，。；\u4e00-\u9fa5·])\s*(?P<name>{NAME})\s*[,，：:]\s*(?P<gender>男|女)\s*[,，]?\s*"
    r"(?:(?P<ethnicity>[\u4e00-\u9fa5]+族)\s*[,，]\s*)?"
    rf"(?P<title>.+?)"
    rf"(?:。|\s*分管工作|(?=\s*{NAME}\s*[,，：:]\s*(?:男|女))|$)"
)
# ldjj2022 profile pane: name line ends with title (duty blocks are separate).
LDJJ_PROFILE_RE = re.compile(
    rf"^\s*(?P<name>{NAME})\s*[,，]\s*(?P<gender>男|女)\s*[,，]?\s*"
    r"(?:(?P<ethnicity>[\u4e00-\u9fa5]+族)\s*[,，]\s*)?"
    r"(?P<title>.+?)\s*$"
)
# Sichuan city hubs: "党委书记、局长张莉主持全面工作。" / "党委委员、副局长姚克安分管…"
SICHUAN_ROLE_NAME_DUTY_RE = re.compile(
    rf"(?P<title>党委书记、局长|党委副书记、局长|"
    rf"党委委员、(?:纪检组组长|总会计师|总经济师|总审计师|副局长|副书记)|"
    rf"(?:一级|二级|三级)?高级主办)"
    rf"\s*(?P<name>{NAME})\s*"
    rf"(?P<action>主持|分管|协助)(?P<body>[^。]*)。"
)
# Fujian-style: "林京华（党委书记、局长）男，汉族，1962年…"
# Xinjiang-style: "石小东（党委书记、局长）\n石小东：男，汉族，…"
HEADING_BIO_RE = re.compile(
    rf"(?P<name>{NAME})\s*[（(](?P<title>[^）)]{{2,40}})[）)]\s*"
    rf"(?:(?P=name)\s*[：:]\s*)?"
    rf"(?P<gender>男|女)\s*[,，]\s*(?:(?P<ethnicity>[\u4e00-\u9fa5]+族)\s*[,，]\s*)?"
)
# Yunnan Pu'er-style: "肖五洲（男，汉族，国家税务总局普洱市税务局党委书记、局长。）"
PAREN_GENDER_BIO_RE = re.compile(
    rf"(?P<name>{NAME})\s*[（(]\s*(?P<gender>男|女)\s*[,，]\s*"
    r"(?:(?P<ethnicity>[\u4e00-\u9fa5]+族)\s*[,，]\s*)?"
    rf"(?P<title>[^）)]+?)[。）)]+"
)
ARTICLE_HREF_RE = re.compile(
    r"(?:20\d{4}/t\d+|t\d+)\.s?html|/ld_\d+|content_[a-f0-9]+\.shtml|"
    r"/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+|ldjianjie\.shtml|ldjs\.shtml|"
    r"/leaderlist/\d+|/010002\d{2}/\d+\.shtml|/web/sj\w+/ldjs\.shtml",
    re.IGNORECASE,
)
# Qinghai / plain: "谭中伟 国家税务总局青海省税务局党委书记、局长" (no gender commas)
INLINE_NAME_BUREAU_TITLE_RE = re.compile(
    rf"(?<![\u4e00-\u9fa5·])(?P<name>{NAME})\s+"
    rf"(?P<title>国家税务总局[\u4e00-\u9fa5]{{2,20}}税务局"
    rf"[^。；\n]{{2,50}}?(?:党委书记、局长|党委委员、副局长|党委委员、纪检组组长|"
    rf"党委委员、总会计师|党委委员、总审计师|党委委员、总经济师|"
    rf"局长|副局长|纪检组组长|总会计师|总审计师|总经济师|一级巡视员))"
)
LDJJ_CHILD_RE = re.compile(
    r"/(?:ldjj|ldzl|ldjs|ldxx\w*|leaderlist|col\d+)/",
    re.IGNORECASE,
)
# Hubei /hbsw/{city}/xxgk/ldjj/: bios live in document.write('姓名，男，…'.replace(...)).
HUBEI_DOC_WRITE_RE = re.compile(
    r"document\.write\s*\(\s*['\"]((?:(?:\\.|[^'\"\\])*?))['\"]\s*\.replace\s*\(",
    re.I,
)
HUBEI_PROFILE_INLINE_RE = re.compile(
    rf"^\s*(?P<name>{NAME})\s*[,，]\s*(?P<gender>男|女)\s*[,，]\s*"
    r"(?:(?P<ethnicity>[\u4e00-\u9fa5]+族)\s*[,，]\s*)?"
    r"(?P<title>.+?)\s*$"
)
SKIP_LINK_TEXT = (
    "首页",
    "信息公开",
    "网站地图",
    "返回",
    "打印",
    "网站声明",
    "网站管理",
    "联系我们",
    "新闻动态",
    "政策文件",
    "纳税服务",
    "互动交流",
    "政策法规",
    "政策解读",
    "新闻宣传",
    "权责清单",
    "行政检查",
    "政府采购",
)
SKIP_PSEUDO_NAMES = (
    "抖音",
    "微博",
    "微信",
    "客户端",
    "简体",
    "繁体",
    "繁體",
    "关怀版",
    "合规经营",
    "隐私声明",
    "个税",
    "发票",
    "小微企业",
    "减税降费",
    "数电票",
    "税费优惠",
    "房产税",
    "印花税",
    "市局频道",
    "市州频道",
    "区县频道",
    "机构职能",
    "机构设置",
    "联系方式",
    "办公时间",
    "办公地址",
    "领导专栏",
    "领导简介",
    "概况信息",
    "新闻动态",
    "专题专栏",
    "主要职能",
    "内设机构",
    "发票查询",
    "总局概况",
    "所得税司",
    "教育中心",
    "新浪微博",
    "新闻发布",
    "查看更多",
    "政策法规",
    "政策解读",
    "新闻宣传",
    "权责清单",
    "行政检查",
    "政府采购",
    "二维码",
    "我要申请",
    "申请须知",
    "中标公告",
    "招标公告",
    "文件解读",
    "预算决算",
    "最新文件",
    "采购意向",
    "执法公示",
    "网站建议",
    "总经济师",
    "总会计师",
    "总审计师",
    "派出机构",
    "直属单位",
    "直属机构",
    "访问统计",
    "重要活动",
    "社保",
    "信息公开",
    "人事司",
    "办公厅",
    "机关党委",
    "稽查局",
    "政策文件",
    "纳税服务",
    "互动交流",
    "长者模式",
    "无障碍浏览",
    "人事任免",
    "人事信息",
    "通知公告",
    "分管工作",
    "联系单位",
    "网站首页",
    "网站纠错",
)
# Require role words; bare "税务" is too broad (news titles like "甘肃税务").
TITLE_MARKERS = ("党委", "局长", "纪检", "总会计", "总审计", "总经济", "副书记", "书记", "巡视员")
ROLE_ONLY_TITLES = (
    "局长",
    "副局长",
    "纪检组组长",
    "纪检组长",
    "总会计师",
    "总审计师",
    "总经济师",
    "党委书记、局长",
    "党委委员、副局长",
    "党委委员、纪检组组长",
    "党委委员、总会计师",
    "党委委员、总审计师",
    "党委委员、总经济师",
)
# Name + title on one link: "王宏伟 党委书记、局长" / "杨 鹏（…党委书记、局长）"
NAME_TITLE_PAREN_RE = re.compile(
    rf"^(?P<name>{NAME})\s*[（(](?P<title>[^）)]{{2,80}})[）)]\s*$"
)
NAME_TITLE_SPACE_RE = re.compile(
    rf"^(?P<name>{NAME})\s+(?P<title>(?:国家税务总局|中共党员)?[\u4e00-\u9fa5、，,]{{2,60}})$"
)
# Xinjiang / Gansu: "党委书记、局长：李杰生" or "副局长：\n李兴国"
TITLE_COLON_NAME_RE = re.compile(
    rf"(?P<title>(?:党委|国家税务总局)?[^：:\n]{{1,40}}?)[：:]\s*(?P<name>{NAME})?"
)
# Alternating lines: 姓名 / 职务 / 姓名 / 职务 (Hebei / Jilin sidebar text)
ALT_NAME_TITLE_RE = re.compile(
    rf"(?:^|[\n\r])\s*(?P<name>{NAME})\s*[\n\r]+\s*(?P<title>[^\n\r]{{2,80}}?)(?=\s*[\n\r]|$)"
)
PERSON_NAME_ONLY_RE = re.compile(rf"^{NAME}$")
LEADER_COL_HREF_RE = re.compile(r"/col/col\d+/|/ld_[a-z]+\.s?html", re.I)
CHROME_MARKERS = (
    "网站地图",
    "网站声明",
    "网站管理",
    "联系我们",
    "主办单位",
    "网站标识码",
    "沪ICP",
    "京ICP",
    "扫一扫",
)


def parse_leader_intro(html: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    soup = BeautifulSoup(html, "html.parser")

    article_meta = soup.select_one("meta[name='i_articleid']")
    article_id = str(article_meta.get("content") or "").strip() if article_meta else ""
    if article_id and article_id != "0" and "liaoning.chinatax.gov.cn" in source_url:
        detail = _parse_liaoning_art_detail(soup, source_url, bureau_code)
        if detail:
            return _filter_leader_duties(detail)

    # Merge multi-person list formats (sidebar / paired role+name / colon) so
    # complementary patterns on one page (e.g. Xinjiang) are not lost.
    # Hub pages like Jiangxi 史峰 / Xizang 任伟: left nav lists ALL leaders while
    # the main pane shows only the selected bio — extract sidebar first.
    sidebar = _parse_leader_hub_sidebar(soup, source_url, bureau_code)
    bios = _merge_duties(
        _parse_hubei_document_write_leaders(soup, html, source_url, bureau_code),
        _parse_hebei_sidebar_list(soup, source_url, bureau_code),
        _parse_liaoning_leader_cards(soup, source_url, bureau_code),
        _parse_paired_role_name_links(soup, source_url, bureau_code),
        _parse_name_title_leader_list(soup, source_url, bureau_code),
        _parse_title_colon_name_list(soup, source_url, bureau_code),
    )

    root = _main_content_root(soup)
    text = _visible_text(root)
    # Always parse the focused bio pane too. Hub/detail pages often list every
    # leader in a sidebar (>=2 stubs) while only the selected person has 分管.
    bios = _merge_duties(
        bios,
        _parse_guangdong_leader_blocks(soup, source_url, bureau_code),
        _parse_liaoning_art_detail(soup, source_url, bureau_code),
        _parse_multi_leader_text(text, source_url, bureau_code),
        _parse_heading_bio_profiles(text, source_url, bureau_code),
        _parse_paren_gender_bios(text, source_url, bureau_code),
        _parse_inline_name_bureau_titles(text, source_url, bureau_code),
        _parse_alternating_name_title_text(text, source_url, bureau_code),
        _parse_sequential_profiles(text, source_url, bureau_code),
        _parse_sichuan_role_name_duties(text, source_url, bureau_code),
    )

    ldjj = soup.select_one(".ldjj2022")
    if ldjj is not None:
        duty = _parse_ldjj2022(ldjj, source_url, bureau_code)
        if duty is not None:
            bios = _merge_duties(bios, [duty])

    # Overlay bio details onto sidebar stubs (sidebar order preserved).
    if sidebar:
        return _filter_leader_duties(_overlay_sidebar_with_bios(sidebar, bios))
    if bios:
        return _filter_leader_duties(bios)
    return []


def _filter_leader_duties(duties: list[LeaderDuty]) -> list[LeaderDuty]:
    return [d for d in duties if is_plausible_person_name(d.person_name)]


def _overlay_sidebar_with_bios(
    sidebar: list[LeaderDuty], bios: list[LeaderDuty]
) -> list[LeaderDuty]:
    by_name = {d.person_name: d for d in bios}
    out: list[LeaderDuty] = []
    seen: set[str] = set()
    for stub in sidebar:
        bio = by_name.get(stub.person_name)
        if bio is None:
            out.append(stub)
        else:
            out.append(
                LeaderDuty(
                    person_name=stub.person_name,
                    gender=bio.gender or stub.gender,
                    ethnicity=bio.ethnicity or stub.ethnicity,
                    title_raw=bio.title_raw or stub.title_raw,
                    duty_summary=bio.duty_summary or stub.duty_summary,
                    departments_raw=bio.departments_raw or stub.departments_raw,
                    source_url=stub.source_url or bio.source_url,
                    bureau_code=stub.bureau_code,
                )
            )
        seen.add(stub.person_name)
    for bio in bios:
        if bio.person_name not in seen:
            out.append(bio)
    return out


def _parse_leader_hub_sidebar(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Extract person-name links from 领导专栏 / 领导简介 left-nav hubs.

    Pages like Jiangxi ``…史峰.html`` and Xizang ``…任伟.html`` list all leaders
    in a sidebar (``.left-box``, ``.ldxx-r``, …); the main pane shows one bio.
    """
    containers: list = []
    for selector in (
        "div.left-box",
        "div.ldxx-r",
        "div.ldxx-l",
        "ul.list_lefnavLdjj",
        "ul.submenu",
        ".mainbox_left",
        ".swxw_left",
        "div.ldjj_left",
        "div.leader-list",
        "ul.leaderlist",
    ):
        for node in soup.select(selector):
            containers.append(node)

    # Heading-anchored walk: find 领导专栏/领导简介 then nearest nav with ≥3 names.
    for heading in soup.find_all(string=re.compile(r"领导专栏|领导简介")):
        cur = heading.parent
        for _ in range(8):
            if cur is None or cur is soup:
                break
            if cur not in containers:
                containers.append(cur)
            cur = getattr(cur, "parent", None)

    best: list[LeaderDuty] = []
    for node in containers:
        duties = _person_name_links_in(node, source_url, bureau_code)
        if len(duties) > len(best):
            best = duties
    return best if len(best) >= 2 else []


def _person_name_links_in(
    node, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    bios = {
        _clean_name(m.group("name")): m
        for m in PROFILE_RE.finditer(_visible_text(node))
    }
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for anchor in node.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:"):
            continue
        label = re.sub(r"[\s\u3000\u2002\u2003]+", "", anchor.get_text(" ", strip=True))
        name = _clean_name(label)
        if not name or name in seen:
            continue
        if name in SKIP_LINK_TEXT or name in SKIP_PSEUDO_NAMES:
            continue
        if not PERSON_NAME_ONLY_RE.fullmatch(name) or not (2 <= len(name) <= 8):
            continue
        if not is_plausible_person_name(name):
            continue
        # Reject bare /col/colN and site-relative chrome (政策法规等)；
        # keep article / ldjj paths, or names already backed by a bio line.
        leaderish_href = bool(
            ARTICLE_HREF_RE.search(href)
            or re.search(r"/(?:ldjj|ldzl|ldjs|ldxx|leaderlist|010002)/", href, re.I)
            or re.search(r"/ld_[a-z]+\.s?html", href, re.I)
        )
        if not leaderish_href and name not in bios:
            continue
        seen.add(name)
        person_url = resolve_list_child_url(source_url, href)
        bio = bios.get(name)
        title = ""
        gender = None
        ethnicity = None
        duty_summary = None
        if bio is not None:
            title = _normalize_title(bio.group("title"))
            gender = bio.group("gender")
            ethnicity = bio.group("ethnicity")
            duty_summary = (
                "主持全面工作"
                if ("书记" in title and "局长" in title)
                else None
            )
        duties.append(
            LeaderDuty(
                person_name=name,
                gender=gender,
                ethnicity=ethnicity,
                title_raw=title,
                duty_summary=duty_summary,
                source_url=person_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _merge_duties(*groups: list[LeaderDuty]) -> list[LeaderDuty]:
    """Merge duties by person name, keeping the richer record (more oversight deps)."""
    by_name: dict[str, LeaderDuty] = {}
    for group in groups:
        for duty in group:
            name = duty.person_name
            prev = by_name.get(name)
            if prev is None:
                by_name[name] = duty
                continue
            if _duty_richness(duty) > _duty_richness(prev):
                by_name[name] = duty
    return list(by_name.values())


def _duty_richness(duty: LeaderDuty) -> tuple[int, int, int]:
    deps = duty.departments_raw or []
    return (
        len(deps),
        1 if (duty.duty_summary or "").strip() else 0,
        len(duty.title_raw or ""),
    )


def leader_detail_urls(html: str, hub_url: str) -> list[str]:
    """Liaoning/JCMS list pages: ``div.tit > a[href*='/art/']`` detail links."""
    soup = BeautifulSoup(html, "html.parser")
    col_id = ""
    col_meta = soup.select_one("meta[name='i_columnid']")
    if col_meta is not None:
        col_id = str(col_meta.get("content") or "").strip()
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("div.tit a[href*='/art/'], div.zhaiyao a[href*='/art/']"):
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:"):
            continue
        if col_id and f"art_{col_id}_" not in href:
            continue
        url = resolve_list_child_url(hub_url, href)
        key = url.split("?", 1)[0].rstrip("/")
        if key in seen or not ARTICLE_HREF_RE.search(key):
            continue
        seen.add(key)
        urls.append(url)
    return urls


def leader_page_targets(html: str, hub_url: str) -> list[str]:
    """Follow META REFRESH / JS location, sidebar leader nav, or article links."""
    refresh_url = extract_meta_refresh_url(html, hub_url) or extract_js_redirect_url(
        html, hub_url
    )
    if refresh_url:
        return [refresh_url]

    urls: list[str] = []
    seen: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")

    def _norm_key(url: str) -> str:
        # Drop cache-busting query so Tianjin ?tid= variants collapse.
        return url.split("?", 1)[0].rstrip("/")

    def add(href: str) -> None:
        if not href or href.startswith("javascript:"):
            return
        url = resolve_list_child_url(hub_url, href)
        key = _norm_key(url)
        if key in seen or key == _norm_key(hub_url):
            return
        seen.add(key)
        urls.append(url)

    def _is_person_label(title: str) -> bool:
        cleaned = _clean_name(title)
        if not cleaned or cleaned in SKIP_LINK_TEXT or cleaned in SKIP_PSEUDO_NAMES:
            return False
        return bool(PERSON_NAME_ONLY_RE.fullmatch(cleaned) and 2 <= len(cleaned) <= 8)

    for anchor in soup.select(
        ".mainbox_left .list a[href], li[id^='ldjj_'] a[href], "
        ".ldjj_name a[href], .ld_li a[href], ul.submenu a[href], "
        "ul.list_lefnavLdjj a[href], div.left-box a[href], div.ldxx-r a[href], "
        "div.ldxx-l a[href], div.ldjj_left a[href]"
    ):
        label = re.sub(r"[\s\u3000\u2002\u2003]+", "", anchor.get_text(" ", strip=True))
        if _is_person_label(label) or any(
            m in str(anchor.get("href") or "") for m in ("ldjj", "ldjs", "ldjianjie", "010002")
        ):
            add(str(anchor.get("href") or ""))

    for anchor in soup.select("a[href]"):
        title = re.sub(r"[\s\u3000\u2002\u2003]+", "", anchor.get_text(" ", strip=True))
        title_attr = re.sub(r"[\s\u3000\u2002\u2003]+", "", str(anchor.get("title") or ""))
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:") or title in SKIP_LINK_TEXT:
            continue
        personish = _is_person_label(title) or _is_person_label(title_attr)
        ldzl_detail = bool(re.search(r"/(?:ldzl|ldjj|ldjs)/.+/content_", href, re.I))
        if not personish and not ldzl_detail:
            continue
        # Person-named leader articles / columns only.
        if (
            ldzl_detail
            or ARTICLE_HREF_RE.search(href)
            or LEADER_COL_HREF_RE.search(href)
            or re.search(r"/(?:ldjj|ldzl|ldjs|ldxx\w*|leaderlist)/", href, re.I)
        ):
            add(href)
    return urls


def html_has_hubei_leader_body(html: str) -> bool:
    """True when page looks like Hubei /hbsw/…/ldjj/ with JS-embedded leader bios."""
    if "xxgkleaderlists" not in html and "leader-body" not in html:
        return False
    return bool(
        HUBEI_DOC_WRITE_RE.search(html)
        and re.search(r"[\u4e00-\u9fa5]{2,4}[，,]\s*[男女]", html)
    )


def _parse_hubei_document_write_leaders(
    soup: BeautifulSoup,
    html: str,
    source_url: str,
    bureau_code: str,
) -> list[LeaderDuty]:
    """Parse Hubei city leader pages with document.write bios + optional .leaderarticle."""
    if not html_has_hubei_leader_body(html):
        return []

    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    list_root = soup.select_one("ul.xxgkleaderlists")
    if list_root is not None:
        for item in list_root.select(":scope > li"):
            profile_text = _hubei_profile_from_node(item)
            if not profile_text:
                continue
            match = HUBEI_PROFILE_INLINE_RE.match(profile_text)
            if match is None:
                continue
            duty_summary, departments = _parse_hubei_leaderarticle(
                item.select_one(".leaderarticle")
            )
            duty = _leader_duty_from_hubei_match(
                match,
                source_url,
                bureau_code,
                seen,
                duty_summary=duty_summary,
                departments=departments,
            )
            if duty is not None:
                duties.append(duty)
        if duties:
            return duties

    for profile_text in _hubei_document_write_profiles(html):
        match = HUBEI_PROFILE_INLINE_RE.match(profile_text)
        if match is None:
            continue
        duty = _leader_duty_from_hubei_match(match, source_url, bureau_code, seen)
        if duty is not None:
            duties.append(duty)
    return duties


def _hubei_profile_from_node(node) -> str:
    for script in node.select("script"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        match = HUBEI_DOC_WRITE_RE.search(raw)
        if match is None:
            continue
        text = match.group(1)
        if re.search(r"[\u4e00-\u9fa5]{2,4}[，,]\s*[男女]", text):
            return text.strip()
    return ""


def _hubei_document_write_profiles(html: str) -> list[str]:
    profiles: list[str] = []
    for match in HUBEI_DOC_WRITE_RE.finditer(html):
        text = match.group(1).strip()
        if re.search(r"[\u4e00-\u9fa5]{2,4}[，,]\s*[男女]", text):
            profiles.append(text)
    return profiles


def _parse_hubei_leaderarticle(node) -> tuple[str | None, list[str]]:
    if node is None:
        return None, []
    duty_summary: str | None = None
    departments: list[str] = []
    liaison_units: list[str] = []
    for group in node.select(".group1"):
        heading = group.select_one("h2")
        if heading is None:
            continue
        title = heading.get_text(" ", strip=True)
        body_node = group.select_one(".jj")
        body = body_node.get_text(" ", strip=True) if body_node else ""
        if not body:
            continue
        if (
            "主持" in title
            or ("主持" in body and "全面" in body)
            or "负责全面工作" in body
        ):
            duty_summary = "主持全面工作"
        elif "联系单位" in title or title.startswith("联系"):
            liaison_units = _split_departments(body)
        elif "分管" in title:
            if "负责全面工作" in body or ("主持" in body and "全面" in body):
                duty_summary = "主持全面工作"
            else:
                duty_summary = duty_summary or "分管工作"
                departments = _split_departments(body)
    if duty_summary is None and liaison_units:
        duty_summary = "主持全面工作"
    return duty_summary, departments or liaison_units


def _leader_duty_from_hubei_match(
    match: re.Match[str],
    source_url: str,
    bureau_code: str,
    seen: set[str],
    *,
    duty_summary: str | None = None,
    departments: list[str] | None = None,
) -> LeaderDuty | None:
    name = _clean_name(match.group("name"))
    title_raw = _normalize_title(match.group("title"))
    if not name or name in seen:
        return None
    if not is_plausible_person_name(name):
        return None
    if not any(key in title_raw for key in TITLE_MARKERS) and title_raw not in ROLE_ONLY_TITLES:
        return None
    seen.add(name)
    if duty_summary is None:
        if "书记" in title_raw and "局长" in title_raw:
            duty_summary = "主持全面工作"
        elif any(
            key in title_raw
            for key in ("副局长", "总会计师", "总审计师", "总经济师", "纪检", "副书记", "巡视员")
        ):
            duty_summary = "分管工作"
    return LeaderDuty(
        person_name=name,
        gender=match.group("gender"),
        ethnicity=match.group("ethnicity"),
        title_raw=title_raw,
        duty_summary=duty_summary,
        departments_raw=departments or [],
        source_url=source_url,
        bureau_code=bureau_code,
    )


def _parse_hebei_sidebar_list(soup: BeautifulSoup, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    """Hebei hub: ``ul.list_lefnavLdjj li a > span(name) + p(title)``."""
    items = soup.select("ul.list_lefnavLdjj li a")
    if not items:
        return []
    bios = _bio_index(soup)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for anchor in items:
        name_node = anchor.select_one("span")
        title_node = anchor.select_one("p")
        name = _clean_name(name_node.get_text(" ", strip=True) if name_node else "")
        title = re.sub(r"\s+", "", title_node.get_text(" ", strip=True) if title_node else "")
        duty = _duty_from_name_title(
            name, title, source_url, bureau_code, bios, seen, href=str(anchor.get("href") or "")
        )
        if duty is not None:
            duties.append(duty)
    return duties


def _parse_name_title_leader_list(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Parse leader lists where each link is ``姓名 职务`` or ``姓名（职务）``."""
    bios = _bio_index(soup)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        if not label or label in SKIP_LINK_TEXT:
            continue
        parsed = _split_name_title_label(label)
        if parsed is None:
            continue
        name, title = parsed
        duty = _duty_from_name_title(
            name, title, source_url, bureau_code, bios, seen, href=str(anchor.get("href") or "")
        )
        if duty is not None:
            duties.append(duty)
    if len(duties) >= 2:
        return duties
    return []


def _parse_paired_role_name_links(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Gansu/Xinjiang: role link and name link share the same href."""
    bios = _bio_index(soup)
    by_url: dict[str, dict[str, str]] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if not href or href.startswith("javascript:"):
            continue
        label = re.sub(r"[\s\u3000\u2002\u2003]+", "", anchor.get_text(" ", strip=True))
        if not label or label in SKIP_LINK_TEXT:
            continue
        url = resolve_list_child_url(source_url, href)
        slot = by_url.setdefault(url, {})
        if label in ROLE_ONLY_TITLES or any(
            label.startswith(role) or label.endswith("：") or label.endswith(":")
            for role in ROLE_ONLY_TITLES
        ):
            # "党委委员、副局长：" style
            title = label.rstrip("：:")
            if any(k in title for k in TITLE_MARKERS) or title in ROLE_ONLY_TITLES:
                slot["title"] = title
        elif PERSON_NAME_ONLY_RE.fullmatch(label) and 2 <= len(label) <= 8:
            slot["name"] = label
            slot["href"] = href
        elif "：" in label or ":" in label:
            # "党委书记、局长：李杰生" on one anchor
            match = TITLE_COLON_NAME_RE.match(label)
            if match and match.group("name"):
                slot["title"] = match.group("title").strip()
                slot["name"] = _clean_name(match.group("name"))
                slot["href"] = href

    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for url, slot in by_url.items():
        name = slot.get("name")
        title = slot.get("title")
        if not name or not title:
            continue
        # Expand short role labels using bureau prefix when bio available
        if title in ROLE_ONLY_TITLES and "党委" not in title and "税务" not in title:
            if title == "局长":
                title = "党委书记、局长"
            elif title == "副局长":
                title = "党委委员、副局长"
            elif title == "纪检组组长":
                title = "党委委员、纪检组组长"
            elif title in {"总会计师", "总审计师", "总经济师"}:
                title = f"党委委员、{title}"
        duty = _duty_from_name_title(
            name, title, source_url, bureau_code, bios, seen, href=slot.get("href") or url
        )
        if duty is not None:
            duties.append(duty)
    return duties


def _parse_title_colon_name_list(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Parse ``职务：姓名`` blocks from visible text (Xinjiang style)."""
    text = _main_content_root(soup).get_text("\n", strip=True)
    text = _trim_chrome(text)
    bios = _bio_index(soup)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    # Join title line with following name-only line when colon has empty name
    lines = [re.sub(r"[\s\u3000\u2002\u2003]+", "", ln) for ln in text.splitlines() if ln.strip()]
    pending_title: str | None = None
    for line in lines:
        if pending_title:
            if PERSON_NAME_ONLY_RE.fullmatch(line) and 2 <= len(line) <= 8:
                duty = _duty_from_name_title(
                    line, pending_title, source_url, bureau_code, bios, seen
                )
                if duty is not None:
                    duties.append(duty)
                pending_title = None
                continue
            pending_title = None
        match = TITLE_COLON_NAME_RE.match(line)
        if match is None:
            continue
        title = match.group("title").strip()
        name = _clean_name(match.group("name") or "")
        if not any(k in title for k in TITLE_MARKERS) and title not in ROLE_ONLY_TITLES:
            continue
        if name:
            duty = _duty_from_name_title(name, title, source_url, bureau_code, bios, seen)
            if duty is not None:
                duties.append(duty)
        else:
            pending_title = title
    return duties


def _parse_inline_name_bureau_titles(
    text: str, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Parse space-joined ``姓名 国家税务总局…税务局…职务`` (Qinghai style)."""
    text = _trim_chrome(text)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for match in INLINE_NAME_BUREAU_TITLE_RE.finditer(text):
        name = _clean_name(match.group("name"))
        title = _normalize_title(match.group("title"))
        # Drop trailing duty crumbs accidentally captured
        title = re.split(r"分管|联系单位|负责", title, maxsplit=1)[0]
        duty = _duty_from_name_title(name, title, source_url, bureau_code, {}, seen)
        if duty is not None:
            duties.append(duty)
    return duties


def _parse_alternating_name_title_text(
    text: str, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Parse ``姓名\\n职务\\n姓名\\n职务`` blocks when bio regexes miss."""
    text = _trim_chrome(text)
    lined = re.sub(r"[ \t]+", "", text)
    lined = re.sub(r"。", "。\n", lined)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for match in ALT_NAME_TITLE_RE.finditer("\n" + lined.replace(" ", "\n")):
        name = _clean_name(match.group("name"))
        title = re.sub(r"\s+", "", match.group("title"))
        duty = _duty_from_name_title(name, title, source_url, bureau_code, {}, seen)
        if duty is not None:
            duties.append(duty)
    return duties if len(duties) >= 2 else []


def _split_name_title_label(label: str) -> tuple[str, str] | None:
    match = NAME_TITLE_PAREN_RE.match(label)
    if match is None:
        match = NAME_TITLE_SPACE_RE.match(label)
    if match is None:
        return None
    name = _clean_name(match.group("name"))
    title = _normalize_title(match.group("title"))
    if not name or not title:
        return None
    return name, title


def _bio_index(soup: BeautifulSoup) -> dict[str, re.Match[str]]:
    return {
        _clean_name(m.group("name")): m
        for m in PROFILE_RE.finditer(_visible_text(_main_content_root(soup)))
    }


def _normalize_title(title: str) -> str:
    title = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", title or "")
    title = re.sub(r"\s+", "", title)
    title = title.strip("。．.;；、，,")
    title = re.sub(r"^中共党员[，,（(]*", "", title)
    title = re.sub(r"^[（(]", "", title)
    title = title.rstrip("）)")
    # Ethnicity accidentally swallowed into title when comma missing.
    title = re.sub(r"^[\u4e00-\u9fa5]{1,3}族(?=国家税务总局)", "", title)
    # Drop accidental leading nav crumbs
    title = re.sub(r"^领导简介", "", title)
    return title


def _duty_from_name_title(
    name: str,
    title: str,
    source_url: str,
    bureau_code: str,
    bios: dict[str, re.Match[str]],
    seen: set[str],
    *,
    href: str = "",
) -> LeaderDuty | None:
    name = _clean_name(name)
    title = _normalize_title(title)
    if not name or not title or name in seen:
        return None
    if name in {"领导简介", "信息公开", "领导专栏", "机构职能"}:
        return None
    if not any(key in title for key in TITLE_MARKERS) and title not in ROLE_ONLY_TITLES:
        return None
    if title in {"领导简介", "信息公开", "人事任免", "领导专栏", "甘肃税务"}:
        return None
    # Reject news-like titles that only matched via loose markers.
    if "税务" in title and not any(k in title for k in ("党委", "局长", "纪检", "总会计", "总审计", "总经济", "书记")):
        return None
    seen.add(name)
    person_url = resolve_list_child_url(source_url, href) if href else source_url
    duty_summary = "主持全面工作" if ("书记" in title and "局长" in title) else None
    if duty_summary is None and any(
        key in title for key in ("副局长", "总会计师", "总审计师", "总经济师", "纪检", "副书记")
    ):
        duty_summary = "分管工作"
    bio = bios.get(name)
    return LeaderDuty(
        person_name=name,
        gender=bio.group("gender") if bio else None,
        ethnicity=bio.group("ethnicity") if bio else None,
        title_raw=title,
        duty_summary=duty_summary,
        source_url=person_url,
        bureau_code=bureau_code,
    )


def _parse_liaoning_leader_cards(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Liaoning city leader list cards: ``div.tit`` + ``div.zhaiyao``."""
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for tit in soup.select("div.tit"):
        anchor = tit.select_one("a[href*='/art/']")
        if anchor is None:
            continue
        label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        parsed = _split_name_title_label(label)
        if parsed is None:
            continue
        name, title = parsed
        href = str(anchor.get("href") or "")
        detail_url = resolve_list_child_url(source_url, href) if href else source_url
        zhaiyao = tit.find_next_sibling("div", class_="zhaiyao")
        bio_text = zhaiyao.get_text(" ", strip=True) if zhaiyao is not None else ""
        gender = ethnicity = None
        bio_match = PROFILE_RE.search(bio_text) or HEADING_BIO_RE.search(bio_text)
        if bio_match is not None:
            gender = bio_match.groupdict().get("gender")
            ethnicity = bio_match.groupdict().get("ethnicity")
        duty = _duty_from_name_title(
            name,
            title,
            detail_url,
            bureau_code,
            {},
            seen,
            href=href,
        )
        if duty is None:
            continue
        if gender:
            duty.gender = gender
        if ethnicity:
            duty.ethnicity = ethnicity
        duties.append(duty)
    return duties if len(duties) >= 2 else []


def _parse_liaoning_art_detail(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Liaoning/JCMS leader detail article (``/art/…/art_873_…``)."""
    if "liaoning.chinatax.gov.cn" not in source_url:
        return []
    title_raw = ""
    for sel in (
        "meta[name='ArticleTitle']",
        "meta[name='ArticleTite']",
        "h1",
        ".content_title",
        ".article_title",
        "title",
    ):
        node = soup.select_one(sel)
        if node is None:
            continue
        title_raw = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        title_raw = re.sub(r"\s+", " ", str(title_raw or "")).strip()
        if title_raw and ("（" in title_raw or "(" in title_raw):
            break
        if title_raw and "领导" not in title_raw[:8]:
            break
    parsed = _split_name_title_label(title_raw.split("_")[0].split("-")[0].strip())
    root = _main_content_root(soup)
    text = _visible_text(root)
    if parsed is None:
        match = HEADING_BIO_RE.search(text) or PROFILE_RE.search(text)
        if match is None:
            return []
        name = _clean_name(match.group("name"))
        title = _normalize_title(match.group("title"))
        gender = match.groupdict().get("gender")
        ethnicity = match.groupdict().get("ethnicity")
    else:
        name, title = parsed
        gender = ethnicity = None
        match = PROFILE_RE.search(text) or HEADING_BIO_RE.search(text)
        if match is not None and _clean_name(match.group("name")) == name:
            gender = match.groupdict().get("gender")
            ethnicity = match.groupdict().get("ethnicity")
    duty_summary, departments = _duty_and_departments(text)
    if not duty_summary and any(k in title for k in ("副局长", "总会计师", "纪检", "总审计", "总经济师")):
        duty_summary = "分管工作"
    if "书记" in title and "局长" in title and not duty_summary:
        duty_summary = "主持全面工作"
    if not is_plausible_person_name(name):
        return []
    return [
        LeaderDuty(
            person_name=name,
            gender=gender,
            ethnicity=ethnicity,
            title_raw=title,
            duty_summary=duty_summary,
            departments_raw=departments,
            source_url=source_url,
            bureau_code=bureau_code,
        )
    ]


def _parse_guangdong_leader_blocks(
    soup: BeautifulSoup, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Guangdong ``leader_content_newType`` / ``leader_content_newType_con`` bios."""
    blocks = soup.select("div.leader_content_newType")
    if not blocks:
        return []
    duty_summary = None
    departments: list[str] = []
    for block in blocks:
        label = block.get_text(" ", strip=True)
        con = block.find_next_sibling("div", class_="leader_content_newType_con")
        body = con.get_text(" ", strip=True) if con else ""
        if "分管" in label or "分管" in body:
            duty_summary = "分管工作"
            departments = _split_departments(f"{label}{body}")
        elif "主持" in label or "主持" in body:
            duty_summary = "主持全面工作"
    if duty_summary is None and not departments:
        return []

    # Name from article title / breadcrumb / first bio sentence.
    name = ""
    for sel in ("meta[name='ArticleTitle']", "h1", ".leader_name", ".content_title", "title"):
        node = soup.select_one(sel)
        if node is None:
            continue
        raw = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
        cleaned = _clean_name(str(raw or "").split("_")[0].split("-")[0])
        # Drop site suffix noise.
        cleaned = re.split(r"国家税务|税务局|领导", cleaned)[0]
        if PERSON_NAME_ONLY_RE.fullmatch(cleaned) and 2 <= len(cleaned) <= 8:
            name = cleaned
            break
    if not name:
        text = _visible_text(soup)
        match = re.search(rf"(?P<name>{NAME})[，,]男", text)
        if match:
            name = _clean_name(match.group("name"))
    if not name or not PERSON_NAME_ONLY_RE.fullmatch(name):
        return []

    title = ""
    title_match = re.search(
        rf"{re.escape(name)}[，,]男[，,]汉族[，,](?P<title>[^。]{{4,80}})",
        _visible_text(soup),
    )
    if title_match:
        title = _normalize_title(title_match.group("title"))

    return [
        LeaderDuty(
            person_name=name,
            gender="男" if "男" in _visible_text(soup)[:200] else None,
            ethnicity=None,
            title_raw=title,
            duty_summary=duty_summary,
            departments_raw=departments,
            source_url=source_url,
            bureau_code=bureau_code,
        )
    ]


def _parse_ldjj2022(node, source_url: str, bureau_code: str) -> LeaderDuty | None:
    profile_node = node.select_one(".ldjjjj")
    if profile_node is None:
        return None
    profile_text = _visible_text(profile_node)
    match = PROFILE_RE.search(profile_text) or LDJJ_PROFILE_RE.search(profile_text)
    if match is None:
        return None

    duty_summary: str | None = None
    departments: list[str] = []
    liaison_units: list[str] = []

    for group in node.select(".ldjjgroup"):
        heading = group.select_one("h2")
        if heading is None:
            continue
        title = heading.get_text(" ", strip=True)
        body = _trim_chrome(_visible_text(group.select_one(".jj") or group))
        if not body:
            continue
        if "主持" in title or ("主持" in body and "全面" in body):
            duty_summary = "主持全面工作"
        elif "联系单位" in title:
            liaison_units = _split_departments(body)
        elif "分管" in title:
            duty_summary = duty_summary or "分管工作"
            departments = _split_departments(body)

    if duty_summary is None and liaison_units:
        duty_summary = "主持全面工作"

    title_raw = _normalize_title(match.group("title"))
    if duty_summary is None and "书记" in title_raw and "局长" in title_raw:
        duty_summary = "主持全面工作"

    return LeaderDuty(
        person_name=_clean_name(match.group("name")),
        gender=match.group("gender"),
        ethnicity=match.group("ethnicity"),
        title_raw=title_raw,
        duty_summary=duty_summary,
        departments_raw=departments or liaison_units,
        source_url=source_url,
        bureau_code=bureau_code,
    )


def _parse_multi_leader_text(text: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    text = _trim_chrome(text)
    matches = list(PROFILE_RE.finditer(text))
    if len(matches) < 2:
        return []

    duties: list[LeaderDuty] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        duty_summary, departments = _duty_and_departments(block)
        duties.append(
            LeaderDuty(
                person_name=_clean_name(match.group("name")),
                gender=match.group("gender"),
                ethnicity=match.group("ethnicity"),
                title_raw=_normalize_title(match.group("title")),
                duty_summary=duty_summary,
                departments_raw=departments,
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _parse_sichuan_role_name_duties(
    text: str, source_url: str, bureau_code: str
) -> list[LeaderDuty]:
    """Parse Sichuan prose: 「党委书记、局长张莉主持全面工作。」"""
    text = _trim_chrome(text)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for match in SICHUAN_ROLE_NAME_DUTY_RE.finditer(text):
        name = _clean_name(match.group("name"))
        title = _normalize_title(match.group("title"))
        if not name or name in seen or not is_plausible_person_name(name):
            continue
        seen.add(name)
        action = match.group("action")
        body = match.group("body") or ""
        if action.startswith("主持"):
            duty_summary = "主持全面工作"
            departments: list[str] = []
        elif action.startswith("协助"):
            duty_summary = "协助工作"
            departments = []
        else:
            duty_summary, departments = _duty_and_departments(f"分管{body}")
        duties.append(
            LeaderDuty(
                person_name=name,
                gender=None,
                ethnicity=None,
                title_raw=title,
                duty_summary=duty_summary,
                departments_raw=departments,
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _parse_heading_bio_profiles(text: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    """Parse '姓名（职务）男，汉族，…' blocks used by Fujian-style pages."""
    text = _trim_chrome(text)
    matches = list(HEADING_BIO_RE.finditer(text))
    if not matches:
        return []
    duties: list[LeaderDuty] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        duty_summary, departments = _duty_and_departments(block)
        title = _normalize_title(match.group("title"))
        duties.append(
            LeaderDuty(
                person_name=_clean_name(match.group("name")),
                gender=match.group("gender"),
                ethnicity=match.group("ethnicity"),
                title_raw=title,
                duty_summary=duty_summary,
                departments_raw=departments,
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _parse_paren_gender_bios(text: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    """Parse Yunnan-style '姓名（男，汉族，…党委书记、局长。）' blocks."""
    text = _trim_chrome(text)
    duties: list[LeaderDuty] = []
    seen: set[str] = set()
    for match in PAREN_GENDER_BIO_RE.finditer(text):
        name = _clean_name(match.group("name"))
        title = _normalize_title(match.group("title"))
        if not name or name in seen or not title:
            continue
        if not any(k in title for k in TITLE_MARKERS) and title not in ROLE_ONLY_TITLES:
            continue
        seen.add(name)
        duties.append(
            LeaderDuty(
                person_name=name,
                gender=match.group("gender"),
                ethnicity=match.group("ethnicity"),
                title_raw=title,
                duty_summary=None,
                departments_raw=[],
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _parse_sequential_profiles(text: str, source_url: str, bureau_code: str) -> list[LeaderDuty]:
    text = _trim_chrome(text)
    matches = list(PROFILE_RE.finditer(text))
    duties: list[LeaderDuty] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        duty_summary, departments = _duty_and_departments(block)
        duties.append(
            LeaderDuty(
                person_name=_clean_name(match.group("name")),
                gender=match.group("gender"),
                ethnicity=match.group("ethnicity"),
                title_raw=_normalize_title(match.group("title")),
                duty_summary=duty_summary,
                departments_raw=departments,
                source_url=source_url,
                bureau_code=bureau_code,
            )
        )
    return duties


def _main_content_root(soup: BeautifulSoup) -> BeautifulSoup:
    for selector in (
        ".ld_con",
        ".xxgk_sjft_con",
        ".xxgk_sjft",
        ".article_body",
        "#zoom",
        ".bt_content",
        ".TRS_Editor",
        ".article",
        ".article_content",
        "#content",
        ".js_article_content",
        "main",
    ):
        node = soup.select_one(selector)
        if node is not None and len(node.get_text(strip=True)) > 50:
            return node
    clone = BeautifulSoup(str(soup), "html.parser")
    for selector in (
        ".mainbox_left",
        ".swxw_left",
        "#footer",
        ".footer",
        ".mfooter",
        "header",
        "nav",
        "#public_footer",
    ):
        for node in clone.select(selector):
            node.decompose()
    return clone.body or clone


def _visible_text(node) -> str:
    text = node.get_text(" ", strip=True)
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", " ", text)
    return re.sub(r"\s+", " ", text)


def _clean_name(name: str) -> str:
    text = re.sub(r"[\s\u3000\u2002\u2003\u200b\u200c\u200d\ufeff]+", "", name or "")
    # Hub cards often use 「江武峰（江武峰）」.
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return text


def _trim_chrome(text: str) -> str:
    cut = len(text)
    for marker in CHROME_MARKERS:
        index = text.find(marker)
        if index == -1:
            continue
        # Share widgets ("微信扫一扫：分享") often sit ABOVE multi-bio bodies
        # (e.g. Xiamen); do not truncate when profiles remain after the marker.
        if marker == "扫一扫" and len(PROFILE_RE.findall(text[index:])) >= 2:
            continue
        cut = min(cut, index)
    return text[:cut].strip()


def _duty_and_departments(block: str) -> tuple[str | None, list[str]]:
    block = _trim_chrome(block)
    if "主持全面工作" in block or re.search(r"主持.{0,12}全面工作", block):
        return "主持全面工作", []
    if "分管工作" in block or "分管" in block:
        return "分管工作", _split_departments(block)
    return None, []


def _split_departments(block: str) -> list[str]:
    chunk = _trim_chrome(block)
    for marker in ("分管工作", "分管"):
        index = chunk.find(marker)
        if index != -1:
            chunk = chunk[index + len(marker) :]
            break
    # Stop before 联系单位 / 联系… blocks (common on province bios).
    for stop in ("联系单位", "联系国家", "联系省", "联系市", "联系区", "，联系", ",联系", "联系"):
        cut = chunk.find(stop)
        if cut != -1 and cut > 0:
            chunk = chunk[:cut]
            break
    chunk = re.sub(r"^[\s：:，,]*分管", "", chunk.strip())
    chunk = chunk.strip("：:。；;，, ")
    parts = [part.strip("。；;，, ") for part in re.split(r"[、]", chunk)]
    out: list[str] = []
    for part in parts:
        if not part or part in {"分管工作", "主持全面工作"}:
            continue
        if "联系" in part:
            part = part.split("联系", 1)[0].strip("，,；; ")
        if not part or part.startswith("联系"):
            continue
        out.append(part)
    return out
