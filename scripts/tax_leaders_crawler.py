from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests import Response
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException, SSLError
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

STA_OVERVIEW_URL = "https://www.chinatax.gov.cn/n810209/index.html"
NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,4}$")
BIO_START_RE = re.compile(r"^(?P<name>[\u4e00-\u9fa5·]{2,4})，(?:男|女)")
TITLE_HINTS = (
    "党委书记",
    "局长",
    "副局长",
    "纪检组组长",
    "总经济师",
    "总会计师",
    "总审计师",
    "党委委员",
    "一级巡视员",
)
LEADER_LINK_KEYWORDS = ("领导简介", "局领导", "领导专栏", "领导信息", "现任领导")
SKIP_LINK_TEXTS = {"国家税务总局", "首页", "信息公开", "领导简介"}

PROVINCIAL_SITES = [
    ("北京市税务局", "https://beijing.chinatax.gov.cn/"),
    ("天津市税务局", "https://tianjin.chinatax.gov.cn/"),
    ("河北省税务局", "https://hebei.chinatax.gov.cn/"),
    ("山西省税务局", "https://shanxi.chinatax.gov.cn/"),
    ("内蒙古自治区税务局", "https://neimenggu.chinatax.gov.cn/"),
    ("辽宁省税务局", "https://liaoning.chinatax.gov.cn/", "https://liaoning.chinatax.gov.cn/col/col6555/index.html"),
    ("吉林省税务局", "https://jilin.chinatax.gov.cn/"),
    ("黑龙江省税务局", "https://heilongjiang.chinatax.gov.cn/"),
    ("上海市税务局", "https://shanghai.chinatax.gov.cn/"),
    ("江苏省税务局", "https://jiangsu.chinatax.gov.cn/", "https://jiangsu.chinatax.gov.cn/col/col7642/index.html"),
    ("浙江省税务局", "https://zhejiang.chinatax.gov.cn/"),
    ("安徽省税务局", "https://anhui.chinatax.gov.cn/"),
    ("福建省税务局", "https://fujian.chinatax.gov.cn/"),
    ("江西省税务局", "https://jiangxi.chinatax.gov.cn/"),
    ("山东省税务局", "https://shandong.chinatax.gov.cn/"),
    ("河南省税务局", "https://henan.chinatax.gov.cn/"),
    ("湖北省税务局", "https://hubei.chinatax.gov.cn/"),
    ("湖南省税务局", "https://hunan.chinatax.gov.cn/"),
    ("广东省税务局", "https://guangdong.chinatax.gov.cn/", "https://guangdong.chinatax.gov.cn/gdsw/ldzl/leader.shtml"),
    ("广西壮族自治区税务局", "https://guangxi.chinatax.gov.cn/"),
    ("海南省税务局", "https://hainan.chinatax.gov.cn/"),
    ("重庆市税务局", "https://chongqing.chinatax.gov.cn/"),
    ("四川省税务局", "https://sichuan.chinatax.gov.cn/"),
    ("贵州省税务局", "https://guizhou.chinatax.gov.cn/", "https://guizhou.chinatax.gov.cn/xxgk/ldjj/"),
    ("云南省税务局", "https://yunnan.chinatax.gov.cn/"),
    ("西藏自治区税务局", "https://xizang.chinatax.gov.cn/"),
    ("陕西省税务局", "https://shaanxi.chinatax.gov.cn/"),
    ("甘肃省税务局", "https://gansu.chinatax.gov.cn/"),
    ("青海省税务局", "https://qinghai.chinatax.gov.cn/"),
    ("宁夏回族自治区税务局", "https://ningxia.chinatax.gov.cn/"),
    ("新疆维吾尔自治区税务局", "https://xinjiang.chinatax.gov.cn/"),
]


@dataclass
class SiteTarget:
    name: str
    url: str
    organization: str
    kind: str


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def decode_response(response: Response) -> str:
    encodings = [response.apparent_encoding, "utf-8", "gb18030"]
    if response.encoding and response.encoding.lower() not in {"iso-8859-1", "latin-1"}:
        encodings.insert(0, response.encoding)
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return response.content.decode(encoding, errors="ignore")
        except LookupError:
            continue
    return response.content.decode("utf-8", errors="ignore")


def fetch_html(session: requests.Session, url: str, timeout: int = 15) -> str:
    errors: list[str] = []
    candidates = [url]
    if url.startswith("https://"):
        candidates.append("http://" + url.removeprefix("https://"))
    for candidate in candidates:
        for verify in (True, False):
            try:
                response = session.get(candidate, timeout=timeout, verify=verify)
                if response.status_code >= 400:
                    raise RequestException(f"HTTP {response.status_code}")
                return decode_response(response)
            except SSLError as exc:
                errors.append(f"{candidate} ssl={verify}: {exc}")
                continue
            except RequestException as exc:
                errors.append(f"{candidate} ssl={verify}: {exc}")
                break
    raise RuntimeError(f"Failed to fetch {url}\n" + "\n".join(errors))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", clean_text(name))


def has_title_hint(text: str) -> bool:
    return any(hint in text for hint in TITLE_HINTS)


def extract_title(text: str) -> str:
    match = re.search(r"(党委书记、局长[^。；]*)", text)
    if match:
        return clean_text(match.group(1))
    match = re.search(
        r"((?:党委委员、)?(?:副局长|纪检组组长|总经济师|总会计师|总审计师)[^。；]*)",
        text,
    )
    if match:
        return clean_text(match.group(1))
    match = re.search(r"(国家税务总局[^。]{0,40}(?:局长|副局长|组长|总经济师|总会计师|总审计师))", text)
    if match:
        return clean_text(match.group(1))
    return ""


def build_profile(organization: str, site_name: str, name: str, title: str, bio: str, source_url: str) -> dict:
    bio = clean_text(bio)
    return {
        "organization": organization,
        "site_name": site_name,
        "name": normalize_name(name),
        "title": clean_text(title),
        "summary": bio[:240],
        "bio": bio,
        "source_url": source_url,
    }


def parse_sta_overview(html: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    profiles: list[dict] = []
    seen: set[str] = set()
    for dt in soup.select("dt"):
        name = normalize_name(dt.get_text(" ", strip=True))
        dd = dt.find_next_sibling("dd")
        title = clean_text(dd.get_text(" ", strip=True)) if dd else ""
        if not NAME_RE.fullmatch(name) or not has_title_hint(title):
            continue
        if name in seen:
            continue
        seen.add(name)
        profiles.append(
            build_profile("国家税务总局", "sta_hq", name, title, f"{name} {title}", source_url)
        )
    return profiles


def parse_bio_pages(html: str, source_url: str, organization: str, site_name: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [clean_text(line) for line in soup.get_text("\n", strip=True).splitlines() if clean_text(line)]
    profiles: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        match = BIO_START_RE.match(line)
        if not match:
            continue
        name = match.group("name")
        title = extract_title(line)
        if not title or name in seen:
            continue
        seen.add(name)
        profiles.append(build_profile(organization, site_name, name, title, line, source_url))
    return profiles


def parse_named_detail_links(html: str, source_url: str, organization: str, site_name: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        text = clean_text(anchor.get_text(" ", strip=True))
        href = urljoin(source_url, anchor.get("href", ""))
        if not href.startswith("http") or "javascript:" in href:
            continue
        name_match = re.match(r"^(?P<name>[\u4e00-\u9fa5·]{2,4})(?:（.+）)?$", text)
        if not name_match:
            continue
        name = name_match.group("name")
        if name in SKIP_LINK_TEXTS or name in seen:
            continue
        if urlparse(href).netloc != urlparse(source_url).netloc:
            continue
        if any(token in href for token in ("ldzl", "ldjj", "leader", "content_", "/col/", "/art/")):
            seen.add(name)
            found.append((name, href))
    return found


def parse_generic_leader_page(html: str, source_url: str, organization: str, site_name: str) -> list[dict]:
    profiles = parse_bio_pages(html, source_url, organization, site_name)
    if profiles:
        return profiles

    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    for dt in soup.select("dt"):
        name = normalize_name(dt.get_text(" ", strip=True))
        dd = dt.find_next_sibling("dd")
        title = clean_text(dd.get_text(" ", strip=True)) if dd else ""
        if NAME_RE.fullmatch(name) and has_title_hint(title) and name not in seen:
            seen.add(name)
            profiles.append(build_profile(organization, site_name, name, title, f"{name} {title}", source_url))
    if profiles:
        return profiles

    for line in [clean_text(node.get_text(" ", strip=True)) for node in soup.select("li, p, td")]:
        match = re.match(r"^(?P<name>[\u4e00-\u9fa5·]{2,4})(?P<title>.+)$", line)
        if not match:
            continue
        name, title = match.group("name"), clean_text(match.group("title"))
        if name in SKIP_LINK_TEXTS or not has_title_hint(title) or len(title) > 40 or name in seen:
            continue
        seen.add(name)
        profiles.append(build_profile(organization, site_name, name, title, f"{name} {title}", source_url))
    return profiles


def discover_leader_url(session: requests.Session, home_url: str, known_url: str | None = None) -> str | None:
    candidates: list[str] = []
    if known_url:
        candidates.append(known_url)
    try:
        html = fetch_html(session, home_url)
    except RuntimeError:
        html = ""
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            text = clean_text(anchor.get_text(" ", strip=True))
            href = urljoin(home_url, anchor.get("href", ""))
            if any(keyword in text for keyword in LEADER_LINK_KEYWORDS) and href.startswith("http"):
                candidates.append(href)
    parsed = urlparse(home_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates.extend(
        [
            urljoin(origin, "/xxgk/ldjj/"),
            urljoin(origin, "/xxgk/ldjj/index.html"),
        ]
    )
    for candidate in unique(candidates):
        try:
            html = fetch_html(session, candidate)
        except RuntimeError:
            continue
        if parse_generic_leader_page(html, candidate, "probe", "probe") or parse_named_detail_links(
            html, candidate, "probe", "probe"
        ):
            return candidate
        title = BeautifulSoup(html, "html.parser").title
        title_text = title.get_text(" ", strip=True) if title else ""
        if "领导" in title_text:
            return candidate
    return known_url or (candidates[0] if candidates else None)


def enrich_detail_profiles(
    session: requests.Session,
    html: str,
    source_url: str,
    organization: str,
    site_name: str,
) -> list[dict]:
    detail_links = parse_named_detail_links(html, source_url, organization, site_name)
    if not detail_links:
        return parse_generic_leader_page(html, source_url, organization, site_name)

    profiles: list[dict] = []
    seen: set[str] = set()
    for name, detail_url in detail_links:
        try:
            detail_html = fetch_html(session, detail_url)
        except RuntimeError as exc:
            logging.warning("Failed detail page for %s: %s", name, exc)
            continue
        parsed = parse_bio_pages(detail_html, detail_url, organization, site_name)
        if parsed:
            for profile in parsed:
                if profile["name"] not in seen:
                    seen.add(profile["name"])
                    profiles.append(profile)
        elif name not in seen:
            title_el = BeautifulSoup(detail_html, "html.parser").select_one("h1, h2, .title, .bt")
            title = clean_text(title_el.get_text(" ", strip=True)) if title_el else ""
            profiles.append(build_profile(organization, site_name, name, title, title or name, detail_url))
            seen.add(name)
        time.sleep(0.2)
    return profiles


def build_targets(session: requests.Session, site_filter: set[str] | None) -> tuple[list[SiteTarget], list[dict]]:
    targets = [
        SiteTarget("sta_hq", STA_OVERVIEW_URL, "国家税务总局", "hq"),
    ]
    skipped: list[dict] = []
    for item in PROVINCIAL_SITES:
        name, home = item[0], item[1]
        known = item[2] if len(item) == 3 else None
        site_name = f"tax_{name}"
        if site_filter and not any(token in site_name or token in name for token in site_filter):
            continue
        try:
            leader_url = discover_leader_url(session, home, known)
        except RuntimeError as exc:
            skipped.append({"site": site_name, "url": home, "reason": str(exc)})
            continue
        if not leader_url:
            skipped.append({"site": site_name, "url": home, "reason": "no public leader page found"})
            continue
        targets.append(SiteTarget(site_name, leader_url, f"国家税务总局{name}", "province"))
        time.sleep(0.2)
    return targets, skipped


def run(output_path: Path, site_filter: set[str] | None = None) -> dict:
    session = create_session()
    logging.info("Discovering tax bureau sites...")
    targets, skipped = build_targets(session, site_filter)
    if site_filter:
        targets = [
            target
            for target in targets
            if target.name in site_filter or any(token in target.name for token in site_filter)
        ]
    logging.info("Prepared %s target pages", len(targets))

    profiles: list[dict] = []
    failed: list[dict] = []
    for target in targets:
        logging.info("Crawling %s -> %s", target.name, target.url)
        try:
            html = fetch_html(session, target.url)
            if target.kind == "hq":
                site_profiles = parse_sta_overview(html, target.url)
            else:
                site_profiles = enrich_detail_profiles(
                    session, html, target.url, target.organization, target.name
                )
            if not site_profiles:
                skipped.append({"site": target.name, "url": target.url, "reason": "leader page found but empty"})
                continue
            profiles.extend(site_profiles)
            logging.info("Fetched %s profiles from %s", len(site_profiles), target.name)
        except Exception as exc:  # noqa: BLE001
            failed.append({"site": target.name, "url": target.url, "error": str(exc)})
            logging.warning("Failed site %s: %s", target.name, exc)
        time.sleep(0.2)

    payload = {
        "profiles_count": len(profiles),
        "sites_targeted": len(targets),
        "profiles": profiles,
        "skipped_sites": skipped,
        "failed_sites": failed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl public tax bureau leader profiles.")
    parser.add_argument("--output", default="output/tax_leader_profiles.json")
    parser.add_argument("--sites", nargs="*", help="Optional filters, e.g. sta_hq 广东 江苏")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")
    payload = run(Path(args.output), set(args.sites) if args.sites else None)
    logging.info("Done. wrote %s profiles to %s", payload["profiles_count"], Path(args.output).resolve())
    logging.info("skipped=%s failed=%s", len(payload["skipped_sites"]), len(payload["failed_sites"]))


if __name__ == "__main__":
    main()
