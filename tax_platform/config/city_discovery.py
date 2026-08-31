"""Discover city / district tax bureau sites under a province host.

Two common layouts:

1. **Shanghai-style** path hubs: ``/{code}/xxgk/rsrm/`` on the same host.
2. **WCM 频道网格** (Shandong / Jiangsu / Hubei / Chongqing / Tianjin / Xinjiang …): province footer
   blocks labelled 「市局频道」「市州频道」「区县频道」「区局频道」「地州频道」 etc.  Link shapes include
   ``/col/col40/index.html`` (济南), ``/hbsw/wuhan/index.html`` (湖北),
   ``/qxtax/wz/`` (重庆区县), ``/sjpd/gys/`` (贵州市局),
   ``/11241000000/index.jsp`` (天津区局), ``/ylz/`` (新疆地州).  City pages expose
   人事任免 / 领导简介 as further columns or ``/xxgk/`` paths.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from tax_platform.config.sites import get_site
from tax_platform.crawler.http_client import create_session, ensure_trailing_slash, fetch_html
from tax_platform.crawler.xxgk_list import (
    extract_xxgk_shell,
    parse_xxgk_tree_labels,
    resolve_xxgk_infotype,
)

# Shanghai-style: /pdtax/xxgk/rsrm/  or /hptax/xxgk/ldjj/
_SUBSITE_PATH_RE = re.compile(
    r"/(?P<code>[a-z][a-z0-9]{1,12})/xxgk/(?:rsrm|rsxx|ldjj|ldjs)(?:/|$)",
    re.I,
)
_COL_PATH_RE = re.compile(r"/col/col(?P<id>\d+)(?:/index\.html)?(?:[?#]|$)", re.I)
_HBSW_PATH_RE = re.compile(r"/hbsw/(?P<code>[a-z][a-z0-9]{1,20})/(?:index\.html)?(?:[?#]|$)", re.I)
_HBSW_XXGK_PATH_RE = re.compile(
    r"/hbsw/(?P<code>[a-z][a-z0-9]{1,20})/xxgk/(?:rsrm|rsxx|ldjj|ldjs)(?:/|$)",
    re.I,
)
_QXTAX_PATH_RE = re.compile(r"/qxtax/(?P<code>[a-z][a-z0-9]{1,12})/(?:[?#]|$)", re.I)
_QXTAX_HOME_RE = re.compile(r"/qxtax/(?P<slug>[a-z][a-z0-9]{1,12})/?$", re.I)
# Guizhou 市局频道: /sjpd/gys/ … /sjpd/gaxqgwh/
_SJPD_PATH_RE = re.compile(r"/sjpd/(?P<code>[a-z][a-z0-9]{1,20})/(?:index\.html)?(?:[?#]|$)", re.I)
_QXTAX_FBFL_JS = "https://chongqing.chinatax.gov.cn/qxtax/images/qx-message.js"
_QX_FBFL_BY_SLUG: dict[str, int] | None = None
_CHANNEL_LABEL_RE = re.compile(
    r"(?:市局|区县|市州|地市|地州|区局|分局|下属|各市|下级|各分局|直属)频道"
)
_TIANJIN_HOST_MARKER = "tianjin.chinatax.gov.cn"
_TIANJIN_PROVINCE_FJDM = "11200000000"
_TIANJIN_FJDM_RE = re.compile(r"^/(?P<fjdm>112\d{8})/index\.jsp(?:/)?$", re.I)
_XJ_HOST_MARKER = "xinjiang.chinatax.gov.cn"
# Xinjiang prefecture: /ylz/ylzxxgk/ylz_28558/fdzdgknr/zsjs/rsrm_22397/
# or /htdq/xxgk/htdq_31801/fdzdgknr/zsjs/rsrm_22397/
_XJ_XXGK_APPT_RE = re.compile(
    r"/(?P<slug>[a-z][a-z0-9]{1,12})/(?:\1xxgk|[a-z]{2,12}xxgk|xxgk)/\1_\d+/fdzdgknr/zsjs/rsrm_\d+(?:/|$)",
    re.I,
)
_XJ_XXGK_LEADER_RE = re.compile(
    r"/(?P<slug>[a-z][a-z0-9]{1,12})/(?:\1xxgk|[a-z]{2,12}xxgk|xxgk)/(?:\1_\d+/fdzdgknr/jggk/)?ldjj(?:/|$)",
    re.I,
)
_XJ_HOME_RE = re.compile(
    r"^/(?P<slug>[a-z][a-z0-9]{1,12})/(?:index\.(?:html?|shtml))?(?:/)?$",
    re.I,
)
_SLUG_PATH_RE = re.compile(
    r"^/(?P<slug>[a-z][a-z0-9]{1,24})(?:/index\.(?:html?|shtml))?(?:/)?$",
    re.I,
)
_FJ_SSWJ_RE = re.compile(r"^/(?P<slug>[a-z]{2,8})sswj/?$", re.I)
_GD_GDSW_RE = re.compile(
    r"^/gdsw/(?P<slug>[a-z]{2,8}sw)/(?:\1_index\.shtml)?/?$",
    re.I,
)
_ABBR_INDEX_RE = re.compile(
    r"^/(?P<slug>[a-z]{2,6})/index\.(?:html?|shtml)(?:/)?$",
    re.I,
)
_SON_PATH_RE = re.compile(r"^/son/(?P<slug>[a-z]{2,12}(?:-\d+)?)(?:/)?$", re.I)
_APPT_HINT = re.compile(r"人事(?:任免|信息)|任免工作人员|rsrm|rsxx", re.I)
_LEADER_HINT = re.compile(r"领导(?:简介|介绍)|ldjj|ldjs", re.I)
_XXGK_HINT = re.compile(
    r"信息公开|机构概况|机构职能|法定主动公开|政务公开|人事信息|人事任免|领导简介|领导介绍",
    re.I,
)
_ZWGK_HINT = re.compile(r"政务公开|zwgk", re.I)
_STATUTORY_HINT = re.compile(r"法定主动公开")
_SKIP_FOLLOW = re.compile(r"公开指南|公开制度|公开年报|公开申请|信息查询|发票真伪")
_APPOINT_LIST_BODY = re.compile(r"任免工作人员")
_BUREAU_NAME = re.compile(r"([\u4e00-\u9fa5]{2,12}(?:市|区|县|盟|旗)?税务局)")
# Footer 市局频道 city labels: 济南 / 南京 / 菏泽
_SHORT_CITY = re.compile(r"^[\u4e00-\u9fff]{2,6}$")
_SKIP_CITY_LABELS = frozenset(
    {
        "首页",
        "更多",
        "返回",
        "关闭",
        "相关链接",
        "市局频道",
        "市州频道",
        "区县频道",
        "区局频道",
        "地市频道",
        "分局频道",
        "下属频道",
        "各市频道",
        "下级频道",
        "各分局频道",
        "直属频道",
        "地州频道",
        "稽查局",
        "友情链接",
        "友情连接",
        "网站链接",
        "纳税服务",
        "办税服务",
        "12366",
        "区县信息公开",
        "信息公开",
        "网站地图",
        "联系我们",
        "人事任免",
        "领导简介",
        "领导介绍",
        "机构概况",
        "主要职责",
        "机构设置",
        "联系方式",
        "政策解读",
        "政策文件",
        "图解税收",
        "税收统计",
        "人事管理",
        "预算决算",
        "公务员招录",
        "网站监管",
        "行政检查",
        "建议提案办理",
        # Nav / service labels that were mis-discovered as city hubs (esp. Liaoning WCM).
        "通知公告",
        "纳税咨询",
        "办税指南",
        "办税日历",
        "优化营商环境",
        "时政要闻",
        "银税互动",
        "发票举报",
        "互动交流",
        "意见征集",
        "新闻动态",
        "热点问答",
        "纳税人学堂",
        "基层动态",
        "我要查询",
        "隐私声明",
        "媒体视点",
        "专题专栏",
        "下载中心",
        "长者专区",
        "三分局",
        "第三税务分局",
    }
)
_BAD_CITY_FRAGMENTS = (
    "人事",
    "政策",
    "机构",
    "领导",
    "网站",
    "预算",
    "税收",
    "招录",
    "检查",
    "公开",
    "解读",
    "统计",
    "图解",
    "提案",
    "文件",
    "职责",
    "设置",
    "联系",
    "概况",
    "监管",
    "频道",
    "链接",
    "稽查",
    "友情",
    "服务",
    "税务",
    "政府",
    "财政",
    "人社",
    "通知",
    "公告",
    "咨询",
    "指南",
    "日历",
    "营商",
    "要闻",
    "互动",
    "举报",
    "征集",
    "动态",
    "问答",
    "学堂",
    "查询",
    "隐私",
    "媒体",
    "专题",
    "专栏",
    "下载",
    "长者",
)


@dataclass
class CitySiteCandidate:
    code: str
    name: str
    level: str
    parent_code: str
    home_url: str
    appointment_list_url: str | None = None
    leader_intro_url: str | None = None
    region: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_entry(self) -> dict:
        return asdict(self)


def discover_city_sites_from_html(
    html: str,
    page_url: str,
    *,
    parent_code: str,
    region: str | None = None,
) -> list[CitySiteCandidate]:
    """Parse one HTML page for child bureau xxgk paths (Shanghai-style, offline-friendly)."""
    soup = BeautifulSoup(html, "html.parser")
    by_code: dict[str, CitySiteCandidate] = {}
    base_host = urlparse(page_url).netloc

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        text = anchor.get_text(" ", strip=True)
        if not href or href.startswith("javascript:"):
            continue
        url = urljoin(page_url, href)
        if not _same_tax_host(page_url, url, base_host):
            continue
        match = _SUBSITE_PATH_RE.search(urlparse(url).path)
        if not match:
            continue
        code = match.group("code").lower()
        if code in {"xxgk", "web", "col", "static", "images"}:
            continue
        name = _guess_name(text, code)
        level = _guess_level(name)
        cand = by_code.get(code)
        if cand is None:
            home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/{code}/"
            cand = CitySiteCandidate(
                code=code,
                name=name,
                level=level,
                parent_code=parent_code,
                home_url=ensure_trailing_slash(home),
                region=region,
            )
            by_code[code] = cand
        path = urlparse(url).path.lower()
        if _APPT_HINT.search(path) or _APPT_HINT.search(text):
            cand.appointment_list_url = _normalize_xxgk_url(url, prefer="rsrm")
        if _LEADER_HINT.search(path) or _LEADER_HINT.search(text):
            cand.leader_intro_url = _normalize_xxgk_url(url, prefer="ldjj")

    if _is_xinjiang_host(page_url):
        _merge_xinjiang_candidates(by_code, soup, page_url, parent_code=parent_code, region=region)

    for cand in by_code.values():
        if _is_xinjiang_host(cand.home_url):
            continue
        base = cand.home_url.rstrip("/")
        if not cand.appointment_list_url:
            cand.appointment_list_url = ensure_trailing_slash(f"{base}/xxgk/rsrm/")
            cand.notes.append("appointment_url_guessed")
        if not cand.leader_intro_url:
            cand.leader_intro_url = ensure_trailing_slash(f"{base}/xxgk/ldjj/")
            cand.notes.append("leader_url_guessed")
    return sorted(by_code.values(), key=lambda c: c.code)


def discover_city_channel_hubs(
    html: str,
    page_url: str,
    *,
    parent_code: str,
    region: str | None = None,
) -> list[CitySiteCandidate]:
    """Parse province footer 频道 grids (市局/市州/区县 …) into child bureau hubs."""
    soup = BeautifulSoup(html, "html.parser")
    by_code: dict[str, CitySiteCandidate] = {}
    base_host = urlparse(page_url).netloc

    channel_roots = _channel_root_nodes(soup)
    # Only trust anchors under a 频道 block — never scan the whole page
    # (appointment/leader sidebars have many short labels that look like city names).
    if not channel_roots:
        return []
    anchors = []
    for root in channel_roots:
        anchors.extend(root.select("a[href]"))

    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("javascript:"):
            continue
        url = urljoin(page_url, href)
        if not _same_tax_host(page_url, url, base_host):
            continue
        hub = _parse_channel_hub_url(url)
        if not hub:
            continue
        text = _anchor_channel_label(anchor)
        if text in _SKIP_CITY_LABELS:
            continue
        if text and (not _SHORT_CITY.match(text) or any(x in text for x in _BAD_CITY_FRAGMENTS)):
            continue
        if not text:
            text = hub.get("fallback_label") or ""
        if not text or text in _SKIP_CITY_LABELS:
            continue
        if not _SHORT_CITY.match(text) or any(x in text for x in _BAD_CITY_FRAGMENTS):
            continue
        code = f"{parent_code}_{hub['code_suffix']}"
        if code in by_code:
            continue
        bureau_name = _city_bureau_name(text)
        cand = CitySiteCandidate(
            code=code,
            name=bureau_name,
            level=_guess_level(bureau_name),
            parent_code=parent_code,
            home_url=hub["home_url"],
            region=region,
            notes=["city_channel_hub"],
        )
        _apply_tianjin_hr_urls(cand)
        by_code[code] = cand
    return sorted(by_code.values(), key=lambda c: c.code)


def enrich_city_hub_urls(
    html: str,
    page_url: str,
    cand: CitySiteCandidate,
) -> CitySiteCandidate:
    """Fill appointment / leader column URLs from a city hub (or related) page."""
    appt, leader, xxgk = _extract_hr_links(html, page_url)
    if appt and not cand.appointment_list_url:
        cand.appointment_list_url = appt
        cand.notes.append("appointment_from_hub")
    if leader and not cand.leader_intro_url:
        cand.leader_intro_url = leader
        cand.notes.append("leader_from_hub")
    if xxgk:
        cand.notes.append(f"xxgk_seen:{xxgk}")
    return cand


def discover_province_children(
    parent_code: str,
    *,
    delay: float = 0.2,
    session=None,
    deepen: bool = True,
    deepen_workers: int = 1,
) -> list[CitySiteCandidate]:
    """Fetch province home (+ hubs) and discover child bureau sites."""
    parent = get_site(parent_code)
    owns_session = session is None
    session = session or create_session()
    pages = [
        parent.home_url,
        parent.appointment_list_url,
        parent.leader_intro_url,
        urljoin(parent.home_url, "/xxgk/"),
    ]
    merged: dict[str, CitySiteCandidate] = {}
    try:
        home_html = ""
        home_url = parent.home_url
        for page in pages:
            if not page:
                continue
            if delay:
                time.sleep(delay)
            try:
                final_url, html = fetch_html(session, page, follow_meta_refresh=True)
            except Exception:  # noqa: BLE001
                continue
            for cand in discover_city_sites_from_html(
                html,
                final_url,
                parent_code=parent_code,
                region=parent.region,
            ):
                _merge_candidate(merged, cand)
            # 市局频道 only on the province home page.
            if page.rstrip("/") == parent.home_url.rstrip("/") or page == parent.home_url:
                home_html = html
                home_url = final_url
                for cand in discover_city_channel_hubs(
                    html,
                    final_url,
                    parent_code=parent_code,
                    region=parent.region,
                ):
                    _merge_candidate(merged, cand)

        if home_html:
            for cand in discover_city_channel_hubs(
                home_html,
                home_url,
                parent_code=parent_code,
                region=parent.region,
            ):
                _merge_candidate(merged, cand)

        if deepen:
            to_deepen = []
            for code, cand in list(merged.items()):
                if (
                    "city_channel_hub" not in cand.notes
                    and cand.appointment_list_url
                    and cand.leader_intro_url
                ):
                    continue
                to_deepen.append(cand)

            def _deepen_one(cand: CitySiteCandidate) -> CitySiteCandidate:
                s = create_session()
                try:
                    _deepen_candidate(s, cand, delay=delay)
                    return cand
                finally:
                    s.close()

            workers = max(1, min(deepen_workers, len(to_deepen) or 1))
            if workers == 1:
                for cand in to_deepen:
                    _deepen_one(cand)
                    merged[cand.code] = cand
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    for cand in pool.map(_deepen_one, to_deepen):
                        merged[cand.code] = cand
    finally:
        if owns_session and hasattr(session, "close"):
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
    merged.pop(parent_code, None)
    for code, cand in list(merged.items()):
        if _is_provincial_subsite_duplicate(cand, parent):
            merged.pop(code, None)
    if parent_code == "xinjiang":
        for cand in discover_xinjiang_prefecture_fallback(
            parent_code,
            region=parent.region,
        ):
            _merge_candidate(merged, cand)
    return sorted(merged.values(), key=lambda c: c.code)


def candidates_to_entries(candidates: list[CitySiteCandidate]) -> list[dict]:
    return [c.to_entry() for c in candidates]


def _deepen_candidate(session, cand: CitySiteCandidate, *, delay: float) -> None:
    """BFS hub → 信息公开 / 法定主动公开 / 人事任免 / 领导简介.

    Shandong city hubs often land on a statutory shell (``col8707``) whose
    sidebar is an xxgk ``tree.jsp``; 人事任免 is ``search.jsp`` + ``infotypeId``
    (e.g. ``A2001``), exposed as ``?number=A2001`` on the shell URL.

    Hubei ``/hbsw/{slug}/`` city hubs expose 人事任免 at a predictable
    ``/hbsw/{slug}/xxgk/rsrm/index.html`` path (法定主动公开 sidebar), not
    ``/{slug}/xxgk/rsrm/`` — probe those URLs first before generic xxgk shells.

    Chongqing ``/qxtax/{slug}/`` districts expose 政务公开标准目录 at ``zwgk/``.
    The 人事任免 sidebar node is a ``span.flnode`` (not ``<a>``); list rows
    load via ``/api/queryGwxxQx`` keyed by ``fbfldm`` from ``qx-message.js``.
    """
    _resolve_qxtax_zwgk_urls(session, cand, delay=delay)
    if _apply_tianjin_hr_urls(cand):
        if cand.appointment_list_url and cand.leader_intro_url:
            return
    seeds: list[str] = []
    hbsw_appt, hbsw_leader = _guess_hbsw_xxgk_urls(cand.home_url)
    if hbsw_appt:
        seeds.extend([u for u in (hbsw_appt, hbsw_leader) if u])
        cand.notes.append("hbsw_xxgk_guess")
    if _is_xinjiang_host(cand.home_url):
        seeds.extend(_xinjiang_xxgk_seeds(cand.home_url))
        cand.notes.append("xinjiang_xxgk_guess")
    seeds.append(cand.home_url)
    qxtax_slug = _qxtax_slug_from_home(cand.home_url)
    if qxtax_slug:
        base = ensure_trailing_slash(cand.home_url.rstrip("/"))
        seeds.extend([f"{base}zwgk/", f"{base}jgzn/"])
    tried: set[str] = set()
    max_pages = 12
    while seeds and len(tried) < max_pages:
        url = seeds.pop(0)
        key = _canon_url(url)
        if not url or key in tried:
            continue
        tried.add(key)
        if delay:
            time.sleep(delay)
        try:
            final_url, html = fetch_html(session, url, follow_meta_refresh=True)
        except Exception:  # noqa: BLE001
            continue

        if not cand.appointment_list_url and _page_looks_like_qxtax_zwgk_shell(html):
            _resolve_qxtax_zwgk_urls(session, cand, delay=0)
        if not cand.appointment_list_url and _page_looks_like_appointment_list(html):
            appt_url = _normalize_list_url(final_url)
            if _accept_as_appointment_url(appt_url, cand):
                cand.appointment_list_url = appt_url
                cand.notes.append("appointment_from_page")
        if not cand.leader_intro_url and _page_looks_like_leader_hub(html):
            cand.leader_intro_url = _normalize_col_url(final_url)
            cand.notes.append("leader_from_page")

        enrich_city_hub_urls(html, final_url, cand)
        _resolve_xinjiang_links_from_html(cand, html, final_url)
        _resolve_xxgk_shell_urls(session, cand, final_url, html, delay=delay)

        if cand.appointment_list_url and cand.leader_intro_url:
            break

        for nxt in _follow_urls_for_deepen(html, final_url, cand=cand):
            nk = _canon_url(nxt)
            if nk not in tried and nk not in {_canon_url(s) for s in seeds}:
                if len(tried) + len(seeds) < max_pages + 8:
                    seeds.append(nxt)

    if not cand.appointment_list_url:
        cand.notes.append("appointment_url_missing")
    if not cand.leader_intro_url:
        cand.notes.append("leader_url_missing")


def _resolve_xxgk_shell_urls(
    session,
    cand: CitySiteCandidate,
    page_url: str,
    html: str,
    *,
    delay: float,
) -> None:
    """If page is an xxgk statutory shell, map tree labels to list URLs."""
    shell = extract_xxgk_shell(html, page_url)
    if not shell or not shell.get("tree_url"):
        return
    if delay:
        time.sleep(delay)
    try:
        _, tree_html = fetch_html(session, shell["tree_url"], referer=page_url, follow_meta_refresh=False)
    except Exception:  # noqa: BLE001
        return
    labels = parse_xxgk_tree_labels(tree_html)
    if not labels:
        return
    # Prefer shells whose tree root matches this city (skip district shells).
    root = re.search(
        r"d\.add\(0\s*,\s*-1\s*,\s*0\s*,\s*'<a[^>]*>([^<]+)</a>'\)",
        tree_html,
    )
    root_name = root.group(1) if root else ""
    city_key = _city_match_key(cand.name)
    if city_key and root_name and city_key not in root_name and root_name not in cand.name:
        # District / other city shell — keep looking.
        if "区" in root_name or "县" in root_name:
            return

    base = _normalize_col_url(page_url.split("?")[0])
    appt_id = resolve_xxgk_infotype(labels, want="appointment")
    leader_id = resolve_xxgk_infotype(labels, want="leader")
    if appt_id and not cand.appointment_list_url:
        cand.appointment_list_url = f"{base}?number={appt_id}"
        cand.notes.append(f"appointment_xxgk:{appt_id}")
    if leader_id and not cand.leader_intro_url:
        # Prefer dedicated leader column when already found; else xxgk shell.
        cand.leader_intro_url = f"{base}?number={leader_id}"
        cand.notes.append(f"leader_xxgk:{leader_id}")


def _city_match_key(name: str) -> str:
    text = name or ""
    text = text.replace("国家税务总局", "").replace("税务局", "")
    return text.strip()


def _page_looks_like_qxtax_zwgk_shell(html: str) -> bool:
    if "zwgkml.js" not in html and "法定主动" not in html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("span.flnode, .flnode"):
        text = re.sub(r"\s+", "", node.get_text(" ", strip=True))
        if text == "人事任免":
            return True
    return False


def _qxtax_slug_from_home(home_url: str) -> str | None:
    match = _QXTAX_HOME_RE.search(urlparse(home_url).path or "")
    return match.group("slug").lower() if match else None


def _qxtax_zwgk_appt_url(home_url: str, fbfl_dm: int) -> str:
    base = ensure_trailing_slash(home_url.rstrip("/"))
    return f"{base}zwgk/index.html?fbfldm={fbfl_dm}"


def _load_qxtax_fbfl_map(session, *, delay: float = 0) -> dict[str, int]:
    global _QX_FBFL_BY_SLUG
    if _QX_FBFL_BY_SLUG is not None:
        return _QX_FBFL_BY_SLUG
    if delay:
        time.sleep(delay)
    try:
        _, js = fetch_html(session, _QXTAX_FBFL_JS, follow_meta_refresh=False)
    except Exception:  # noqa: BLE001
        _QX_FBFL_BY_SLUG = {}
        return _QX_FBFL_BY_SLUG
    out: dict[str, int] = {}
    for block in re.findall(r"\{[^{}]+\}", js):
        loa = re.search(r'loa:"([^"]+)"', block)
        newfldm = re.search(r"newfldm:(\d+)", block)
        if loa and newfldm:
            out[loa.group(1).lower()] = int(newfldm.group(1))
    _QX_FBFL_BY_SLUG = out
    return out


def _resolve_qxtax_zwgk_urls(session, cand: CitySiteCandidate, *, delay: float) -> None:
    slug = _qxtax_slug_from_home(cand.home_url)
    if not slug or cand.appointment_list_url:
        return
    fbfl_map = _load_qxtax_fbfl_map(session, delay=delay)
    newfldm = fbfl_map.get(slug)
    if not newfldm:
        return
    # Sidebar order: 机构设置=newfldm+0, 人事任免=newfldm+1 (see qx-message.js).
    appt_fbfl = newfldm + 1
    cand.appointment_list_url = _qxtax_zwgk_appt_url(cand.home_url, appt_fbfl)
    cand.notes.append(f"appointment_qxtax_zwgk:{appt_fbfl}")


def _page_looks_like_appointment_list(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for name in ("ColumnName", "channel", "ColumnKeywords"):
        meta = soup.select_one(f'meta[name="{name}"]')
        content = (meta.get("content") if meta else "") or ""
        if "人事任免" in content:
            return True
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if "人事任免" in title:
        return True
    # List pages usually show many appointment notice titles.
    if len(_APPOINT_LIST_BODY.findall(html)) >= 2:
        return True
    return False


def _page_looks_like_leader_hub(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    for name in ("ColumnName", "channel", "ColumnKeywords"):
        meta = soup.select_one(f'meta[name="{name}"]')
        content = (meta.get("content") if meta else "") or ""
        if _LEADER_HINT.search(content):
            return True
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if _LEADER_HINT.search(title):
        return True
    # Hub with several leader profile links / duty labels.
    if html.count("党委") >= 3 and ("副局长" in html or "局长" in html):
        if "领导" in title or "ldjj" in title.lower():
            return True
    return False


def _follow_urls_for_deepen(
    html: str,
    page_url: str,
    *,
    cand: CitySiteCandidate | None = None,
) -> list[str]:
    """Prioritized next hops; skip 指南/制度 noise; prefer city-matched 法定主动公开."""
    soup = BeautifulSoup(html, "html.parser")
    priority: list[str] = []
    statutory: list[str] = []
    info: list[str] = []
    seen: set[str] = set()
    city_key = _city_match_key(cand.name) if cand else ""

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("javascript:"):
            continue
        text = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
        if not text or not _XXGK_HINT.search(text):
            continue
        if _SKIP_FOLLOW.search(text):
            continue
        url = _normalize_list_url(urljoin(page_url, href))
        key = _canon_url(url)
        if key in seen:
            continue
        seen.add(key)

        # Prefer statutory links near the city name in surrounding text.
        context = ""
        parent = anchor.parent
        for _ in range(3):
            if parent is None:
                break
            context += parent.get_text(" ", strip=True) if hasattr(parent, "get_text") else ""
            parent = getattr(parent, "parent", None)

        if "/fdzdgknr/zsjs/rsrm" in urlparse(url).path.lower():
            priority.insert(0, url)
        elif _APPT_HINT.search(text) or _LEADER_HINT.search(text) or text == "人事信息":
            priority.append(url)
        elif _ZWGK_HINT.search(text) or "/zwgk/" in urlparse(url).path.lower():
            statutory.insert(0, url)
        elif _STATUTORY_HINT.search(text):
            if city_key and city_key in context:
                statutory.insert(0, url)
            else:
                statutory.append(url)
        elif "信息公开" in text or "机构概况" in text:
            info.append(url)

    ordered = priority + statutory[:2] + info[:2]
    return ordered


def _extract_hr_links(html: str, page_url: str) -> tuple[str | None, str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    appt = leader = xxgk = None
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("javascript:"):
            continue
        text = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
        url = urljoin(page_url, href)
        path = urlparse(url).path
        useful = bool(
            _COL_PATH_RE.search(path)
            or _SUBSITE_PATH_RE.search(path)
            or _HBSW_XXGK_PATH_RE.search(path)
            or _XJ_XXGK_APPT_RE.search(path)
            or _XJ_XXGK_LEADER_RE.search(path)
            or _XXGK_HINT.search(text)
            or _ZWGK_HINT.search(text)
            or "/zwgk/" in path.lower()
            or "/fdzdgknr/" in path.lower()
            or "u_zlmview" in path.lower()
            or _APPT_HINT.search(text)
            or _LEADER_HINT.search(text)
        )
        if not useful:
            continue
        xj_appt = _normalize_xinjiang_appt_url(url)
        if appt is None and (
            xj_appt
            or _APPT_HINT.search(text)
            or text == "人事任免"
            or _HBSW_XXGK_PATH_RE.search(path) and "/rsrm" in path.lower()
            or "/fdzdgknr/zsjs/rsrm" in path.lower()
            or ("u_zlmviewmx.action" in path.lower() and _APPT_HINT.search(text))
        ):
            appt = xj_appt or _normalize_list_url(url)
        elif _LEADER_HINT.search(text) and leader is None:
            leader = _normalize_list_url(url)
        elif leader is None and _LEADER_HINT.search(text) and "u_zlmview.action" in path.lower():
            leader = _normalize_list_url(url)
        elif xxgk is None and (
            _XXGK_HINT.search(text)
            or _ZWGK_HINT.search(text)
            or "/zwgk/" in path.lower()
        ):
            xxgk = _normalize_list_url(url)
    return appt, leader, xxgk


def _canon_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = parsed.query
    # Keep number= for xxgk shells; drop other query noise for dedupe.
    if query:
        qs = parse_qs(query)
        if "number" in qs and qs["number"]:
            query = f"number={qs['number'][0]}"
        else:
            query = ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def _is_provincial_subsite_duplicate(cand: CitySiteCandidate, parent) -> bool:
    """Drop provincial bureau home paths mistaken for city subsites (e.g. cqtax, hbsw)."""
    parent_home = _canon_url((parent.home_url or "").rstrip("/"))
    cand_home = _canon_url((cand.home_url or "").rstrip("/"))
    if parent_home and cand_home == parent_home:
        return True
    if cand.code in {"cqtax", "hbsw", "xjsw", "sjpd"} and cand.parent_code == parent.code:
        return True
    return False


def _channel_root_nodes(soup: BeautifulSoup) -> list:
    roots: list = []
    seen: set[int] = set()

    def _add_root(parent) -> None:
        if parent is None or not hasattr(parent, "select"):
            return
        pid = id(parent)
        if pid in seen:
            return
        links = parent.select("a[href]")
        if len(links) >= 3:
            seen.add(pid)
            roots.append(parent)

    for node in soup.find_all(string=_CHANNEL_LABEL_RE):
        parent = getattr(node, "parent", None)
        for _ in range(8):
            if parent is None:
                break
            _add_root(parent)
            parent = getattr(parent, "parent", None)

    # Tab / footer blocks: label in title, alt, or nearby heading without exact text node.
    for tag in soup.select("[title], [alt], h2, h3, h4, span, div, li, dt"):
        label = (tag.get("title") or tag.get("alt") or tag.get_text(" ", strip=True) or "").strip()
        if not label or not _CHANNEL_LABEL_RE.search(label):
            continue
        parent = tag
        for _ in range(8):
            if parent is None:
                break
            _add_root(parent)
            parent = getattr(parent, "parent", None)

    return roots


_HBSW_SKIP_SLUGS = frozenset({"xxgk", "gkml", "art", "index", "search", "hdjl", "xwdt"})
_SLUG_SKIP = frozenset(
    {
        "xxgk",
        "gkml",
        "art",
        "index",
        "search",
        "gdsw",
        "static",
        "images",
        "web",
        "col",
        "cqtax",
        "hbsw",
        "qxtax",
        "sjpd",
        "zfxxgkzl",
        "zfxxgk",
        "son",
        "gk",
        "wsbs",
        "hd",
        "hdjl",
        "xwdt",
        "bszn",
        "h5",
        "api",
        "upload",
        "download",
        "english",
        "en",
        "mobile",
        "m",
        "rss",
        "sitemap",
    }
)


def _anchor_channel_label(anchor) -> str:
    text = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
    if text:
        return text
    for script in anchor.find_all("script"):
        src = script.string or script.get_text()
        match = re.search(r"document\.write\('([^']+)'", src or "")
        if not match:
            continue
        raw = match.group(1)
        for sep in ("市税务局", "省税务局", "税务局", "区税务局", "县税务局"):
            if sep in raw:
                return raw.split(sep)[0]
        return raw
    title = (anchor.get("title") or "").strip()
    if title:
        return re.sub(r"\s+", "", title)
    img = anchor.select_one("img")
    if img:
        alt = (img.get("alt") or img.get("title") or "").strip()
        if alt:
            return re.sub(r"\s+", "", alt)
    return ""


def _parse_channel_hub_url(url: str) -> dict | None:
    parsed = urlparse(url)
    path = parsed.path or ""
    scheme = "https" if parsed.scheme in {"http", "https", ""} else parsed.scheme
    netloc = parsed.netloc

    col = _COL_PATH_RE.search(path)
    if col:
        col_id = col.group("id")
        return {
            "code_suffix": f"col{col_id}",
            "home_url": f"{scheme}://{netloc}/col/col{col_id}/index.html",
            "fallback_label": None,
        }

    hbsw = _HBSW_PATH_RE.search(path)
    if hbsw:
        slug = hbsw.group("code").lower()
        if slug in _HBSW_SKIP_SLUGS:
            return None
        return {
            "code_suffix": f"hbsw_{slug}",
            "home_url": f"{scheme}://{netloc}/hbsw/{slug}/index.html",
            "fallback_label": None,
        }

    qxtax = _QXTAX_PATH_RE.search(path)
    if qxtax:
        slug = qxtax.group("code").lower()
        if slug in {"xxgk", "static", "images"}:
            return None
        return {
            "code_suffix": f"qxtax_{slug}",
            "home_url": ensure_trailing_slash(f"{scheme}://{netloc}/qxtax/{slug}/"),
            "fallback_label": None,
        }

    sjpd = _SJPD_PATH_RE.search(path)
    if sjpd:
        slug = sjpd.group("code").lower()
        if slug in _SLUG_SKIP:
            return None
        return {
            "code_suffix": f"sjpd_{slug}",
            "home_url": ensure_trailing_slash(f"{scheme}://{netloc}/sjpd/{slug}/"),
            "fallback_label": None,
        }

    fj = _FJ_SSWJ_RE.search(path)
    if fj:
        slug = fj.group("slug").lower()
        if slug in _SLUG_SKIP:
            return None
        return {
            "code_suffix": f"fj_{slug}sswj",
            "home_url": ensure_trailing_slash(f"{scheme}://{netloc}/{slug}sswj/"),
            "fallback_label": None,
        }

    gd = _GD_GDSW_RE.search(path)
    if gd:
        slug = gd.group("slug").lower()
        return {
            "code_suffix": f"gd_{slug}",
            "home_url": f"{scheme}://{netloc}/gdsw/{slug}/{slug}_index.shtml",
            "fallback_label": None,
        }

    son = _SON_PATH_RE.search(path)
    if son:
        slug = son.group("slug").lower()
        return {
            "code_suffix": f"son_{slug.replace('-', '_')}",
            "home_url": f"{scheme}://{netloc}/son/{slug}/",
            "fallback_label": None,
        }

    abbr = _ABBR_INDEX_RE.search(path)
    if abbr:
        slug = abbr.group("slug").lower()
        if slug in _SLUG_SKIP:
            return None
        return {
            "code_suffix": f"abbr_{slug}",
            "home_url": f"{scheme}://{netloc}/{slug}/index.html",
            "fallback_label": None,
        }

    tj = _TIANJIN_FJDM_RE.search(path)
    if tj:
        fjdm = tj.group("fjdm")
        return {
            "code_suffix": f"fjdm_{fjdm}",
            "home_url": f"{scheme}://{netloc}/{fjdm}/index.jsp",
            "fallback_label": None,
        }

    slug_match = _SLUG_PATH_RE.search(path)
    if slug_match:
        slug = slug_match.group("slug").lower()
        if slug in _SLUG_SKIP or slug.endswith("sw"):
            return None
        if path.count("/") > 2:
            return None
        return {
            "code_suffix": f"path_{slug}",
            "home_url": ensure_trailing_slash(f"{scheme}://{netloc}/{slug}/"),
            "fallback_label": None,
        }
    return None


def _merge_candidate(merged: dict[str, CitySiteCandidate], cand: CitySiteCandidate) -> None:
    prev = merged.get(cand.code)
    if prev is None:
        merged[cand.code] = cand
        return
    if cand.appointment_list_url and (
        not prev.appointment_list_url
        or "guessed" in ",".join(prev.notes)
        or "missing" in ",".join(prev.notes)
    ):
        prev.appointment_list_url = cand.appointment_list_url
    if cand.leader_intro_url and (
        not prev.leader_intro_url
        or "guessed" in ",".join(prev.notes)
        or "missing" in ",".join(prev.notes)
    ):
        prev.leader_intro_url = cand.leader_intro_url
    if cand.name and cand.name != cand.code and len(cand.name) >= len(prev.name or ""):
        prev.name = cand.name
    for note in cand.notes:
        if note not in prev.notes:
            prev.notes.append(note)


def _same_tax_host(page_url: str, url: str, base_host: str) -> bool:
    host = urlparse(url).netloc
    if not host:
        return True
    if host == base_host:
        return True
    return host.endswith(".chinatax.gov.cn") and host == urlparse(page_url).netloc


def _guess_name(link_text: str, code: str) -> str:
    text = (link_text or "").strip()
    m = _BUREAU_NAME.search(text)
    if m:
        return m.group(1)
    if text and len(text) <= 20 and "税务" in text:
        return text
    return code


def _city_bureau_name(short: str) -> str:
    text = (short or "").strip()
    if not text:
        return "市税务局"
    if text.endswith("税务局"):
        return text if text.startswith("国家税务总局") else f"国家税务总局{text}"
    if text.endswith(("区", "县", "旗", "盟", "市")):
        return f"国家税务总局{text}税务局"
    return f"国家税务总局{text}市税务局"


def _guess_level(name: str) -> str:
    if any(x in name for x in ("区", "县", "旗", "盟")):
        return "district"
    if "市" in name:
        return "city"
    return "city"


def _normalize_xxgk_url(url: str, *, prefer: str) -> str:
    parsed = urlparse(url)
    match = _SUBSITE_PATH_RE.search(parsed.path)
    if not match:
        return ensure_trailing_slash(url)
    code = match.group("code")
    root = f"{parsed.scheme}://{parsed.netloc}/{code}/xxgk/{prefer}/"
    return ensure_trailing_slash(root)


def _hbsw_slug_from_home(home_url: str) -> str | None:
    match = _HBSW_PATH_RE.search(urlparse(home_url or "").path)
    return match.group("code").lower() if match else None


def _guess_hbsw_xxgk_urls(home_url: str) -> tuple[str | None, str | None]:
    """Hubei city hubs: ``/hbsw/{slug}/xxgk/rsrm/index.html`` etc."""
    slug = _hbsw_slug_from_home(home_url)
    if not slug:
        return None, None
    parsed = urlparse(home_url)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc
    base = f"{scheme}://{netloc}/hbsw/{slug}"
    return (
        f"{base}/xxgk/rsrm/index.html",
        f"{base}/xxgk/ldjj/index.html",
    )


def _accept_as_appointment_url(url: str, cand: CitySiteCandidate) -> bool:
    """Hubei xxgk index pages mention 人事任免 but list lives under ``/rsrm/``."""
    path = urlparse(url).path.lower()
    if _xinjiang_slug_from_home(cand.home_url):
        return "/fdzdgknr/zsjs/rsrm" in path
    if not _hbsw_slug_from_home(cand.home_url):
        return True
    return "/rsrm" in path or "rsrm" in path


def _normalize_hbsw_xxgk_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _HBSW_XXGK_PATH_RE.search(parsed.path):
        return None
    scheme = "https" if parsed.scheme in {"http", "https", ""} else parsed.scheme
    path = parsed.path or "/"
    if path.endswith("/"):
        path = f"{path}index.html"
    elif not path.endswith(".html"):
        path = f"{path}/index.html"
    return urlunparse((scheme, parsed.netloc.lower(), path, "", "", ""))


def _normalize_list_url(url: str) -> str:
    xj = _normalize_xinjiang_appt_url(url)
    if xj:
        return xj
    hbsw = _normalize_hbsw_xxgk_url(url)
    if hbsw:
        return hbsw
    return _normalize_col_url(url)


def _is_tianjin_host(url: str) -> bool:
    return _TIANJIN_HOST_MARKER in (urlparse(url).netloc or "").lower()


def _tianjin_fjdm_from_url(url: str) -> str | None:
    parsed = urlparse(url or "")
    match = _TIANJIN_FJDM_RE.search(parsed.path or "")
    if match:
        return match.group("fjdm")
    qs = parse_qs(parsed.query or "")
    fjdm = (qs.get("fjdm") or [None])[0]
    if fjdm and re.fullmatch(r"112\d{8}", fjdm):
        return fjdm
    return None


def _tianjin_hr_urls(fjdm: str, *, scheme: str, netloc: str) -> tuple[str, str]:
    base = f"{scheme}://{netloc}"
    if fjdm == _TIANJIN_PROVINCE_FJDM:
        leader = f"{base}/u_zlmView.action?fjdm={fjdm}&lmdm=010002"
        appt = f"{base}/u_zlmViewMx.action?fjdm={fjdm}&lmdm=01000401"
        return leader, appt
    # District leader hub (lists all 班子 members); detail .shtml pages are children.
    leader = f"{base}/mv_showTitles.action?fjdm={fjdm}&lmdm=010002"
    appt = f"{base}/u_zlmViewMx.action?fjdm={fjdm}&lmdm=01000501"
    return leader, appt


def _apply_tianjin_hr_urls(cand: CitySiteCandidate) -> bool:
    """Construct leader/appt URLs for Tianjin fjdm district hubs."""
    if not _is_tianjin_host(cand.home_url):
        return False
    fjdm = _tianjin_fjdm_from_url(cand.home_url)
    if not fjdm or fjdm == _TIANJIN_PROVINCE_FJDM:
        return False
    parsed = urlparse(cand.home_url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or _TIANJIN_HOST_MARKER
    leader, appt = _tianjin_hr_urls(fjdm, scheme=scheme, netloc=netloc)
    if not cand.leader_intro_url:
        cand.leader_intro_url = leader
        cand.notes.append("leader_tianjin_fjdm")
    if not cand.appointment_list_url:
        cand.appointment_list_url = appt
        cand.notes.append("appointment_tianjin_fjdm")
    return bool(cand.leader_intro_url and cand.appointment_list_url)


def _is_xinjiang_host(url: str) -> bool:
    return _XJ_HOST_MARKER in (urlparse(url).netloc or "").lower()


def _xinjiang_slug_from_home(home_url: str) -> str | None:
    match = _XJ_HOME_RE.search(urlparse(home_url or "").path or "")
    return match.group("slug").lower() if match else None


def _xinjiang_xxgk_seeds(home_url: str) -> list[str]:
    slug = _xinjiang_slug_from_home(home_url)
    if not slug:
        return []
    base = ensure_trailing_slash(home_url.rstrip("/"))
    return [
        f"{base}{slug}xxgk/",
        f"{base}xxgk/",
        f"{base}{slug}xxgk/ldjj/",
    ]


def _normalize_xinjiang_appt_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _XJ_XXGK_APPT_RE.search(parsed.path or ""):
        return None
    rsrm_match = re.search(r"(/fdzdgknr/zsjs/rsrm_\d+/)", parsed.path or "", re.I)
    if not rsrm_match:
        return None
    path = (parsed.path or "/")[: rsrm_match.end()]
    scheme = "http" if parsed.scheme in {"http", "https", ""} else parsed.scheme
    return ensure_trailing_slash(
        urlunparse((scheme, parsed.netloc.lower(), path, "", "", ""))
    )


def _normalize_xinjiang_leader_url(url: str) -> str | None:
    parsed = urlparse(url)
    if not _XJ_XXGK_LEADER_RE.search(parsed.path or ""):
        return None
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = f"{path}/"
    scheme = "http" if parsed.scheme in {"http", "https", ""} else parsed.scheme
    return urlunparse((scheme, parsed.netloc.lower(), path, "", "", ""))


def _xinjiang_candidate_code(parent_code: str, slug: str) -> str:
    return f"{parent_code}_xj_{slug}"


def _merge_xinjiang_candidates(
    by_code: dict[str, CitySiteCandidate],
    soup: BeautifulSoup,
    page_url: str,
    *,
    parent_code: str,
    region: str | None,
) -> None:
    """Collect Xinjiang prefecture hubs and rsrm list URLs from xxgk sidebars."""
    base_host = urlparse(page_url).netloc
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href or href.startswith("javascript:"):
            continue
        url = urljoin(page_url, href)
        if not _same_tax_host(page_url, url, base_host):
            continue
        path = urlparse(url).path or ""
        appt_match = _XJ_XXGK_APPT_RE.search(path)
        leader_match = _XJ_XXGK_LEADER_RE.search(path)
        home_match = _XJ_HOME_RE.search(path)
        slug = None
        if appt_match:
            slug = appt_match.group("slug").lower()
        elif leader_match:
            slug = leader_match.group("slug").lower()
        elif home_match:
            slug = home_match.group("slug").lower()
        if not slug or slug in _SLUG_SKIP:
            continue
        code = _xinjiang_candidate_code(parent_code, slug)
        text = anchor.get_text(" ", strip=True)
        name = _guess_name(text, slug) if text else slug
        if name == slug:
            name = _city_bureau_name(text) if text else f"{slug}税务局"
        cand = by_code.get(code)
        if cand is None:
            scheme = urlparse(url).scheme or "http"
            home = ensure_trailing_slash(f"{scheme}://{urlparse(url).netloc}/{slug}/")
            cand = CitySiteCandidate(
                code=code,
                name=name,
                level=_guess_level(name),
                parent_code=parent_code,
                home_url=home,
                region=region,
                notes=["xinjiang_prefecture"],
            )
            by_code[code] = cand
        appt_url = _normalize_xinjiang_appt_url(url)
        if appt_url:
            cand.appointment_list_url = appt_url
            cand.notes.append("appointment_xinjiang_xxgk")
        leader_url = _normalize_xinjiang_leader_url(url)
        if leader_url:
            cand.leader_intro_url = leader_url
            cand.notes.append("leader_xinjiang_xxgk")


def _resolve_xinjiang_links_from_html(
    cand: CitySiteCandidate,
    html: str,
    page_url: str,
) -> None:
    if not _is_xinjiang_host(page_url) and not _is_xinjiang_host(cand.home_url):
        return
    appt, leader, _ = _extract_hr_links(html, page_url)
    if appt and not cand.appointment_list_url and _accept_as_appointment_url(appt, cand):
        cand.appointment_list_url = appt
        cand.notes.append("appointment_xinjiang_deepen")
    if leader and not cand.leader_intro_url:
        cand.leader_intro_url = leader
        cand.notes.append("leader_xinjiang_deepen")


# Known 地州频道 slugs (from 地州频道 + xxgk shells). Used when province home is WAF-blocked.
# Each row: slug, shell_id, display name, xxgk segment under /{slug}/ (usually xxgk or {slug}xxgk).
XINJIANG_PREFECTURE_HUBS: tuple[tuple[str, str, str, str], ...] = (
    ("ylz", "28558", "伊犁哈萨克自治州", "ylzxxgk"),
    ("htdq", "31801", "和田地区", "xxgk"),
    ("ksdq", "30843", "喀什地区", "xxgk"),
    ("wlmq", "30041", "乌鲁木齐市", "xxgk"),
    ("cj", "29763", "昌吉回族自治州", "xxgk"),
    ("klmy", "29609", "克拉玛依市", "xxgk"),
    ("aks", "30872", "阿克苏地区", "xxgk"),
    ("tlf", "30351", "吐鲁番市", "xxgk"),
    ("bygl", "30503", "巴音郭楞蒙古自治州", "xxgk"),
    ("alt", "29237", "阿勒泰地区", "xxgk"),
    ("betl", "29208", "博尔塔拉蒙古自治州", "xxgk"),
    ("hms", "30474", "哈密市", "xxgk"),
    ("kz", "31243", "克孜勒苏柯尔克孜自治州", "xxgk"),
    ("tcdq", "28960", "塔城地区", "tcxxgk"),
    ("kfq", "32392", "乌鲁木齐经济技术开发区", "xxgk"),
    ("gxq", "32423", "乌鲁木齐高新技术产业开发区", "xxgk"),
    ("tmg", "32330", "铁门关", "xxgk"),
    ("wjq", "32206", "五家渠", "xxgk"),
    ("ale", "32237", "阿拉尔", "xxgk"),
    ("shz", "32083", "石河子", "xxgk"),
)


def discover_xinjiang_prefecture_fallback(
    parent_code: str,
    *,
    region: str | None = None,
    host: str = _XJ_HOST_MARKER,
) -> list[CitySiteCandidate]:
    """Seed prefecture hubs with constructed xxgk rsrm URLs when live discovery is blocked."""
    out: list[CitySiteCandidate] = []
    for slug, shell_id, short_name, xxgk_seg in XINJIANG_PREFECTURE_HUBS:
        home = ensure_trailing_slash(f"http://{host}/{slug}/")
        appt = (
            f"http://{host}/{slug}/{xxgk_seg}/{slug}_{shell_id}/fdzdgknr/zsjs/rsrm_22397/"
        )
        if xxgk_seg.endswith("xxgk") and xxgk_seg != "xxgk":
            leader = f"http://{host}/{slug}/{xxgk_seg}/ldjj/"
        else:
            leader = f"http://{host}/{slug}/xxgk/{slug}_{shell_id}/fdzdgknr/jggk/ldjj/"
        name = _city_bureau_name(short_name)
        out.append(
            CitySiteCandidate(
                code=_xinjiang_candidate_code(parent_code, slug),
                name=name,
                level=_guess_level(name),
                parent_code=parent_code,
                home_url=home,
                appointment_list_url=ensure_trailing_slash(appt),
                leader_intro_url=ensure_trailing_slash(leader),
                region=region,
                notes=["xinjiang_prefecture_fallback"],
            )
        )
    return out


def _normalize_col_url(url: str) -> str:
    parsed = urlparse(url)
    match = _COL_PATH_RE.search(parsed.path)
    if not match:
        return url.split("#")[0]
    scheme = "https" if parsed.scheme in {"http", "https", ""} else parsed.scheme
    base = f"{scheme}://{parsed.netloc}/col/col{match.group('id')}/index.html"
    qs = parse_qs(parsed.query)
    number = (qs.get("number") or [None])[0]
    if number:
        return f"{base}?number={number}"
    return base
