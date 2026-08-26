"""Discover city / district tax bureau sites under a province host.

Two common layouts:

1. **Shanghai-style** path hubs: ``/{code}/xxgk/rsrm/`` on the same host.
2. **WCM 市局频道** (Shandong / Jiangsu …): province footer links like
   ``/col/col40/index.html`` (济南), then city pages expose 人事任免 / 领导简介
   as further ``/col/colXXXX/`` columns (often under 机构概况 sidebar).
"""

from __future__ import annotations

import re
import time
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
_APPT_HINT = re.compile(r"人事(?:任免|信息)|任免工作人员|rsrm|rsxx", re.I)
_LEADER_HINT = re.compile(r"领导(?:简介|介绍)|ldjj|ldjs", re.I)
_XXGK_HINT = re.compile(
    r"信息公开|机构概况|机构职能|法定主动公开|人事信息|人事任免|领导简介|领导介绍",
    re.I,
)
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
    "税务",
    "政府",
    "财政",
    "人社",
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

    for cand in by_code.values():
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
    """Parse province 「市局频道」links → ``/col/colN/`` city hubs."""
    soup = BeautifulSoup(html, "html.parser")
    by_code: dict[str, CitySiteCandidate] = {}
    base_host = urlparse(page_url).netloc

    channel_roots = _channel_root_nodes(soup)
    # Only trust anchors under a 「市局频道」block — never scan the whole page
    # (appointment/leader sidebars have many short labels that look like city names).
    if not channel_roots:
        return []
    anchors = []
    for root in channel_roots:
        anchors.extend(root.select("a[href]"))

    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        text = re.sub(r"\s+", "", anchor.get_text(" ", strip=True))
        if not href or href.startswith("javascript:"):
            continue
        if text in _SKIP_CITY_LABELS or not _SHORT_CITY.match(text):
            continue
        if any(x in text for x in _BAD_CITY_FRAGMENTS):
            continue
        url = urljoin(page_url, href)
        if not _same_tax_host(page_url, url, base_host):
            continue
        col = _COL_PATH_RE.search(urlparse(url).path)
        if not col:
            continue
        col_id = col.group("id")
        code = f"{parent_code}_col{col_id}"
        if code in by_code:
            continue
        home = f"{urlparse(url).scheme}://{urlparse(url).netloc}/col/col{col_id}/index.html"
        by_code[code] = CitySiteCandidate(
            code=code,
            name=_city_bureau_name(text),
            level="city",
            parent_code=parent_code,
            home_url=home,
            region=region,
            notes=["city_channel_hub"],
        )
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
            for code, cand in list(merged.items()):
                if (
                    "city_channel_hub" not in cand.notes
                    and cand.appointment_list_url
                    and cand.leader_intro_url
                ):
                    continue
                _deepen_candidate(session, cand, delay=delay)
                merged[code] = cand
    finally:
        if owns_session and hasattr(session, "close"):
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
    merged.pop(parent_code, None)
    return sorted(merged.values(), key=lambda c: c.code)


def candidates_to_entries(candidates: list[CitySiteCandidate]) -> list[dict]:
    return [c.to_entry() for c in candidates]


def _deepen_candidate(session, cand: CitySiteCandidate, *, delay: float) -> None:
    """BFS hub → 信息公开 / 法定主动公开 / 人事任免 / 领导简介.

    Shandong city hubs often land on a statutory shell (``col8707``) whose
    sidebar is an xxgk ``tree.jsp``; 人事任免 is ``search.jsp`` + ``infotypeId``
    (e.g. ``A2001``), exposed as ``?number=A2001`` on the shell URL.
    """
    seeds: list[str] = [cand.home_url]
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

        if not cand.appointment_list_url and _page_looks_like_appointment_list(html):
            cand.appointment_list_url = _normalize_col_url(final_url)
            cand.notes.append("appointment_from_page")
        if not cand.leader_intro_url and _page_looks_like_leader_hub(html):
            cand.leader_intro_url = _normalize_col_url(final_url)
            cand.notes.append("leader_from_page")

        enrich_city_hub_urls(html, final_url, cand)
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
        url = _normalize_col_url(urljoin(page_url, href))
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

        if _APPT_HINT.search(text) or _LEADER_HINT.search(text) or text == "人事信息":
            priority.append(url)
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
            or _XXGK_HINT.search(text)
            or _APPT_HINT.search(text)
            or _LEADER_HINT.search(text)
        )
        if not useful:
            continue
        if (_APPT_HINT.search(text) or text == "人事任免") and appt is None:
            appt = _normalize_col_url(url)
        elif _LEADER_HINT.search(text) and leader is None:
            leader = _normalize_col_url(url)
        elif _XXGK_HINT.search(text) and xxgk is None:
            xxgk = _normalize_col_url(url)
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


def _channel_root_nodes(soup: BeautifulSoup) -> list:
    roots = []
    for node in soup.find_all(string=re.compile(r"市局频道")):
        parent = getattr(node, "parent", None)
        for _ in range(6):
            if parent is None:
                break
            links = parent.select("a[href]") if hasattr(parent, "select") else []
            if len(links) >= 3:
                roots.append(parent)
                break
            parent = getattr(parent, "parent", None)
    return roots


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
    if not text.endswith("市"):
        text = f"{text}市"
    return f"国家税务总局{text}税务局"


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
