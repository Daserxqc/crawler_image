from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import urllib3
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
    "Accept-Language": "zh-CN,zh;q=0.9",
}

REFRESH_RE = re.compile(
    r'content=["\']?\s*\d+\s*;\s*url\s*=\s*([^"\'>\s]+)',
    re.IGNORECASE,
)
# Chongqing qxtax /ldjj/ index shells redirect via JS, e.g.
# window.location.href="./202606/t20260608_383268.html"
JS_LOCATION_RE = re.compile(
    r"""window\.location(?:\.href)?\s*=\s*['"]([^'"]+)['"]"""
    r"""|window\.location\.replace\(\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Ruishu / similar WAF JS challenge markers (HTTP 412 body).
_WAF_MARKERS = ("$_ts", "nsd=", "arg1=", "document.createElement(\"section\")")
_REAL_CONTENT_MARKERS = (
    "领导",
    "税务局",
    "信息公开",
    "人事任免",
    "任免",
    "SiteName",
    "ColumnName",
    "党委书记",
    "分管工作",
)
_STEALTH_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "window.chrome = window.chrome || {runtime: {}};"
)


def ensure_trailing_slash(url: str) -> str:
    """List pages must keep a trailing slash so relative ./yyyyMM/tXXX.html resolves correctly."""
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path += "/"
    return urlunparse(parsed._replace(path=path))


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    adapter = HTTPAdapter(max_retries=Retry(total=0))
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


def looks_like_waf_challenge(html: str, *, status_code: int | None = None) -> bool:
    """True when body is a JS anti-bot challenge rather than real page content.

    Note: Ruishu embeds ``$_ts`` scripts on *successful* pages too, so length +
    content markers matter more than the bare marker.
    """
    if status_code == 412:
        return True
    if status_code == 202 and html and any(m in html[:8000] for m in _WAF_MARKERS):
        return True
    if not html:
        return False
    if "Connection failed" in html[:4000]:
        return True
    # Passed challenge / normal page with embedded anti-bot script.
    if len(html) > 8_000 and any(marker in html for marker in _REAL_CONTENT_MARKERS):
        return False
    sample = html[:8000]
    if "$_ts" in sample and len(html) < 12_000:
        return True
    # Liaoning / some provincial sites: tiny HTML shell with only obfuscated JS.
    if len(html) < 4_000:
        lowered = sample.lower()
        if "<body><script" in lowered.replace(" ", "") or (
            "<script" in lowered and "</body></html>" in lowered and len(html) < 2_500
        ):
            if not any(marker in html for marker in _REAL_CONTENT_MARKERS):
                return True
    hits = sum(1 for marker in _WAF_MARKERS if marker in sample)
    return hits >= 2 and len(html) < 80_000


def _decode_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if text and "\ufffd" not in text[:200]:
            return text
        if text:
            return text
    return raw.decode("utf-8", errors="ignore")


def _html_looks_useful(html: str) -> bool:
    """Reject blank shells and Ruishu challenges; accept real bureau pages."""
    if not html or len(html) < 2_500:
        return False
    if looks_like_waf_challenge(html):
        return False
    # Empty/about:blank-style documents after WAF abort.
    stripped = html.replace(" ", "").replace("\n", "").lower()
    if stripped in {"<html><head></head><body></body></html>", "<html><body></body></html>"}:
        return False
    if "chrome-error://" in html or "chromewebdata" in stripped:
        return False
    if any(marker in html for marker in _REAL_CONTENT_MARKERS) and len(html) >= 2_500:
        return True
    return len(html) > 12_000 and "$_ts" not in html[:3000]


def _find_system_chrome() -> Path | None:
    """Locate a real Chrome/Edge/Chromium binary for CDP fallback."""
    env = (os.environ.get("TAX_HR_CHROME") or os.environ.get("CHROME_PATH") or "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p

    which_names = (
        "google-chrome-stable",
        "google-chrome",
        "chromium-browser",
        "chromium",
        "chrome",
        "msedge",
        "microsoft-edge",
    )
    for name in which_names:
        found = shutil.which(name)
        if found:
            return Path(found)

    candidates = [
        # Linux / cloud
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium-browser"),
        Path("/usr/bin/chromium"),
        Path("/snap/bin/chromium"),
        # Windows
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def _linux_browser_args() -> list[str]:
    """Args required on many headless Linux VMs (Aliyun ECS, Docker, etc.)."""
    if sys.platform.startswith("win"):
        return []
    return ["--no-sandbox", "--disable-dev-shm-usage"]


def _chromium_launch_args(*extra: str) -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--headless=new",
        *_linux_browser_args(),
        *extra,
    ]
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in args:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fetch_html_system_chrome_cdp(
    url: str,
    *,
    timeout: int = 60,
    wait_ms: int = 25_000,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Last-resort fetch via system Chrome + CDP.

    Playwright ``launch(headless=…)`` is fingerprint-blocked on some Ruishu
    list pages (e.g. Beijing). Connecting to a real Chrome process over CDP
    can still pass. Uses a temporary profile and closes Chrome afterwards.
    May briefly show a Chrome window.
    """
    from playwright.sync_api import sync_playwright

    chrome = _find_system_chrome()
    if chrome is None:
        raise RuntimeError("system Chrome/Edge not found for CDP fallback")

    port = _free_port()
    profile = Path(tempfile.mkdtemp(prefix="tax_chrome_cdp_"))
    proc: subprocess.Popen[str] | None = None
    try:
        chrome_args = [
            str(chrome),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--window-size=1366,768",
            *_linux_browser_args(),
            "about:blank",
        ]
        proc = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                ) as resp:
                    if resp.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.3)
        else:
            raise RuntimeError(f"Chrome CDP port {port} did not become ready")

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                logging.debug("system-chrome CDP goto: %s", exc)

            html = ""
            final_url = url
            end = time.time() + max(wait_ms / 1000.0, 8.0)
            while time.time() < end:
                try:
                    html = page.content()
                    final_url = page.url
                except Exception:  # noqa: BLE001
                    html = ""
                if _html_looks_useful(html) and not str(final_url).startswith("chrome-"):
                    break
                time.sleep(0.8)

            if not _html_looks_useful(html) or str(final_url).startswith("chrome-"):
                raise RuntimeError(
                    f"system-chrome CDP still challenge/empty ({len(html)} bytes) url={final_url}"
                )
            cookies = context.cookies()
            logging.info(
                "Browser fetch OK via system-chrome CDP (%s bytes)", len(html)
            )
            return final_url, html, cookies
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def apply_browser_cookies(session: requests.Session, cookies: list[dict[str, Any]]) -> None:
    """Copy Playwright cookies into a requests session for follow-up fetches."""
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        session.cookies.set(
            name,
            value,
            domain=cookie.get("domain") or None,
            path=cookie.get("path") or "/",
        )


def _apply_playwright_stealth(context: Any, page: Any) -> None:
    """Best-effort stealth; missing package is fine."""
    context.add_init_script(_STEALTH_INIT)
    try:
        from playwright_stealth import Stealth

        Stealth().apply_stealth_sync(page)
    except Exception:  # noqa: BLE001
        pass


_XXGK_INPAGE_SEARCH_JS = r"""
return (async () => {
  const area = new URL(location.href).searchParams.get('vc_xxgkarea')
    || new URL(location.href).searchParams.get('area') || '';
  if (!area) return null;
  const tree = await (await fetch(
    '/module/xxgk/tree.jsp?standardXxgk=1&area=' + encodeURIComponent(area) + '&divid=div4',
    {credentials: 'include'}
  )).text();
  let iid = '';
  const re = /funclick\(\s*\\?['"]([A-Za-z0-9]+)\\?['"][^>]*>\s*([^<]{1,40})/gi;
  let m;
  while ((m = re.exec(tree))) {
    const label = (m[2] || '').replace(/\s+/g, '');
    if (label.includes('人事任免') || label.includes('干部任免') || label.includes('任免')) {
      iid = m[1];
      break;
    }
  }
  if (!iid) iid = 'rsglrsrm';
  const text = await (await fetch(
    '/module/xxgk/search.jsp?divid=div4&infotypeId=' + iid
      + '&jdid=1&area=' + encodeURIComponent(area)
      + '&sortfield=createdatetime:0,orderid:0',
    {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: '',
    }
  )).text();
  return {iid, html: text};
})();
"""


def _drission_try_xxgk_list(page: Any, shell_html: str) -> str | None:
    """After WAF passes, pull 人事任免 via in-page search.jsp (Jilin-style shells)."""
    page_url = ""
    try:
        page_url = str(page.url or "")
    except Exception:  # noqa: BLE001
        page_url = ""
    if "vc_xxgkarea=" not in page_url and "vc_xxgkarea=" not in (shell_html or ""):
        return None
    if "任免" in (shell_html or "") and "绿园" in (shell_html or ""):
        # Shell DOM already has the appointment rows we care about.
        pass
    try:
        data = page.run_js(_XXGK_INPAGE_SEARCH_JS)
    except Exception as exc:  # noqa: BLE001
        logging.debug("Drission xxgk in-page search failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    html = data.get("html") or ""
    if not html or "任免" not in html:
        return None
    if len(html) < 500:
        return None
    logging.info(
        "Drission xxgk in-page search OK (iid=%s, %s bytes)",
        data.get("iid"),
        len(html),
    )
    return html


def _drission_try_jpage_list(page: Any, shell_html: str) -> str | None:
    """After WAF shell loads, pull jpage dataproxy list in-page (Jilin province col8211)."""
    from tax_platform.config.list_url_normalize import prefer_http_for_legacy_ssl_hosts
    from tax_platform.crawler.jpage import extract_jpage_html, find_dataproxy_url

    if "param_" not in (shell_html or "") or "dataproxy.jsp" not in (shell_html or ""):
        return None
    if "任免" in (shell_html or "") and "art_" in (shell_html or ""):
        return None
    try:
        page_url = prefer_http_for_legacy_ssl_hosts(str(page.url or ""))
    except Exception:  # noqa: BLE001
        page_url = ""
    proxy = find_dataproxy_url(shell_html, page_url)
    if not proxy:
        return None
    proxy = prefer_http_for_legacy_ssl_hosts(proxy)
    try:
        text = page.run_js(
            """
            const u = arguments[0];
            const qs = u.split('?')[1] || '';
            return fetch(u, {
              method: 'POST',
              credentials: 'include',
              headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
              },
              body: qs,
            }).then(r => r.text());
            """,
            proxy,
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug("Drission jpage in-page dataproxy failed: %s", exc)
        return None
    if not isinstance(text, str) or len(text) < 500:
        return None
    material = extract_jpage_html(text)
    if "任免" not in material and "art_" not in material:
        return None
    logging.info("Drission jpage in-page dataproxy OK (%s bytes)", len(material))
    return material


def _drission_try_fujian_ztree_list(page: Any, shell_html: str) -> str | None:
    """Fujian city xxgk: zTree「人事任免」loads rsxx/rsrm list into the right pane."""
    if "chinatax.gov.cn" not in (page.url or "") and "fujian.chinatax.gov.cn" not in (shell_html or ""):
        # still allow when already on fujian host
        pass
    if "ztree" not in (shell_html or "").lower() and "zTree" not in (shell_html or ""):
        if "主动公开" not in (shell_html or "") and "基本目录" not in (shell_html or ""):
            return None
    # Already has notice rows.
    if "任免工作人员" in (shell_html or "") and re.search(r"t20\d{6}_\d+\.htm", shell_html or ""):
        return shell_html
    try:
        # Prefer zTree node text; fall back to any visible 人事任免 control.
        clicked = page.run_js(
            """
            const nodes = [...document.querySelectorAll('a, span, li')];
            const hit = nodes.find(el => ((el.innerText || el.textContent || '').trim() === '人事任免'));
            if (!hit) return false;
            const a = hit.closest('a') || hit.querySelector('a') || hit;
            a.click();
            return true;
            """
        )
        if not clicked:
            return None
    except Exception as exc:  # noqa: BLE001
        logging.debug("fujian ztree click failed: %s", exc)
        return None
    deadline = time.time() + 12.0
    html = ""
    while time.time() < deadline:
        time.sleep(0.6)
        html = page.html or ""
        if "任免工作人员" in html and re.search(r"t20\d{6}_\d+\.htm", html):
            return html
    return html if "任免工作人员" in html else None


def _fetch_html_drission_once(
    url: str,
    *,
    headless: bool,
    timeout: int = 60,
    wait_ms: int = 20_000,
    user_data_dir: Path | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Single DrissionPage attempt (headless or headed system Chrome)."""
    from DrissionPage import ChromiumOptions, ChromiumPage

    options = ChromiumOptions()
    options.headless(headless)
    options.set_argument("--disable-blink-features=AutomationControlled")
    options.set_argument("--window-size=1920,1080")
    if headless:
        options.set_argument("--headless=new")
    for arg in _linux_browser_args():
        options.set_argument(arg)
    options.set_user_agent(DEFAULT_HEADERS["User-Agent"])
    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.set_user_data_path(str(user_data_dir))
    page = ChromiumPage(options)
    label = "DrissionPage headed" if not headless else "DrissionPage headless"
    try:
        page.get(url, timeout=timeout)
        deadline = time.time() + max(wait_ms / 1000.0, 5.0)
        html = ""
        while time.time() < deadline:
            try:
                page.run_js("if (document.body) document.body.style.display = 'block';")
            except Exception:  # noqa: BLE001
                pass
            html = page.html or ""
            if _html_looks_useful(html):
                # Give AJAX list a short extra window on xxgk shells.
                if "vc_xxgkarea=" in url and "任免" not in html:
                    time.sleep(1.0)
                    html = page.html or ""
                # Shanxi son/list: Vue/jQuery renders rows after shell loads.
                if "/son/list/" in url:
                    list_deadline = time.time() + 20.0
                    while time.time() < list_deadline:
                        html = page.html or ""
                        if "son/detail/" in html and "任免" in html:
                            break
                        if 'id="wzList"' in html and re.search(
                            r'son/detail/[^"\']+', html
                        ):
                            break
                        time.sleep(0.8)
                break
            time.sleep(0.8)
        if not _html_looks_useful(html):
            raise RuntimeError(f"{label} still challenge/empty ({len(html)} bytes)")

        xxgk_html = _drission_try_xxgk_list(page, html)
        if xxgk_html:
            html = xxgk_html
        else:
            jpage_html = _drission_try_jpage_list(page, html)
            if jpage_html:
                html = jpage_html
        if "/son/list/" in (page.url or url) and "son/detail/" not in html:
            from tax_platform.crawler.shanxi_son_list import _drission_try_shanxi_son_list

            sx_html = _drission_try_shanxi_son_list(page, html)
            if sx_html:
                html = sx_html
        # Fujian: jgsz shell + zTree「人事任免」→ rsxx list rows.
        if (
            "fujian.chinatax.gov.cn" in (page.url or url)
            and "任免工作人员" not in html
            and ("/zfxxgkml/" in (page.url or url) or "ztree" in html.lower())
        ):
            fj_html = _drission_try_fujian_ztree_list(page, html)
            if fj_html:
                html = fj_html

        cookies_raw = page.cookies(all_domains=True) or []
        cookies: list[dict[str, Any]] = []
        for item in cookies_raw:
            if isinstance(item, dict) and item.get("name"):
                cookies.append(item)
        logging.info("Browser fetch OK via %s (%s bytes)", label, len(html))
        return page.url or url, html, cookies
    finally:
        try:
            page.quit()
        except Exception:  # noqa: BLE001
            pass


def _fetch_html_drission(url: str, *, timeout: int = 60, wait_ms: int = 20_000) -> tuple[str, str, list[dict[str, Any]]]:
    """Ruishu fallback via system Chrome (DrissionPage).

    Headless is tried first (no window). Some provinces (e.g. Jilin) blank
    headless Playwright/Drission but succeed with a brief headed Chrome window
    — same spirit as system-Chrome CDP for Beijing.
    """
    from tax_platform.paths import output_dir

    profile_root = output_dir()
    errors: list[str] = []
    try:
        return _fetch_html_drission_once(
            url,
            headless=True,
            timeout=timeout,
            wait_ms=min(wait_ms, 12_000),
            user_data_dir=profile_root / ".chrome_drission_headless",
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"headless: {exc}")

    try:
        return _fetch_html_drission_once(
            url,
            headless=False,
            timeout=timeout,
            wait_ms=wait_ms,
            user_data_dir=profile_root / ".chrome_drission_headed",
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"headed: {exc}")
        raise RuntimeError("DrissionPage failed\n" + "\n".join(errors)) from exc


def fetch_html_browser(
    url: str,
    *,
    timeout: int = 60,
    wait_ms: int = 25_000,
    headless: bool | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Fetch URL with a headless browser so Ruishu/412 JS challenges can complete.

    Returns ``(final_url, html, cookies)``.

    Prefers headless Playwright. Blank pages mean WAF detected automation —
    not a broken install. Falls back to DrissionPage (headless, then a brief
    headed system-Chrome window for sites like Jilin), then system-Chrome CDP.
    """
    # Shanxi son/list: AJAX list after shell; Playwright returns empty nav shell.
    if "/son/list/" in url:
        try:
            return _fetch_html_drission(url, timeout=timeout, wait_ms=wait_ms)
        except Exception as exc:  # noqa: BLE001
            logging.debug("son/list Drission fast-path failed for %s: %s", url, exc)

    # Fujian city 主动公开目录: zTree click needed; Playwright often returns shell only.
    if "fujian.chinatax.gov.cn" in url and "/zfxxgkml/" in url:
        try:
            return _fetch_html_drission(url, timeout=timeout, wait_ms=max(wait_ms, 20_000))
        except Exception as exc:  # noqa: BLE001
            logging.debug("fujian zfxxgkml Drission fast-path failed for %s: %s", url, exc)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required for WAF/412 pages. "
            "Run: pip install playwright && playwright install chromium firefox"
        ) from exc

    # HARD RULE: never open a visible browser window (ignore caller headless=False).
    _ = headless
    use_headless = True
    # Real Chrome channel often beats bundled Chromium; still may blank on
    # hard Ruishu sites (e.g. Fujian) — DrissionPage is tried next.
    attempts: list[tuple[str, dict[str, Any]]] = [
        (
            "chromium",
            {
                "headless": use_headless,
                "channel": "chrome",
                "args": _chromium_launch_args(),
            },
        ),
        ("firefox", {"headless": use_headless}),
        (
            "chromium",
            {
                "headless": use_headless,
                "args": _chromium_launch_args(),
            },
        ),
    ]

    errors: list[str] = []
    # Prefer a shorter first-pass wait; escalate only if nothing useful yet.
    first_wait = min(wait_ms, 12_000)

    with sync_playwright() as playwright:
        for index, (engine_name, launch_kwargs) in enumerate(attempts):
            browser = None
            label = (
                f"{engine_name}"
                f"{'+chrome' if launch_kwargs.get('channel') == 'chrome' else ''}"
                f" headless={launch_kwargs.get('headless')}"
            )
            try:
                browser_type = getattr(playwright, engine_name)
                try:
                    browser = browser_type.launch(**launch_kwargs)
                except Exception as launch_exc:  # noqa: BLE001
                    # channel=chrome missing → skip this attempt
                    if launch_kwargs.get("channel"):
                        errors.append(f"{label} launch: {launch_exc}")
                        continue
                    raise
                ua = (
                    DEFAULT_HEADERS["User-Agent"]
                    if engine_name == "chromium"
                    else (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
                        "Gecko/20100101 Firefox/128.0"
                    )
                )
                context = browser.new_context(
                    user_agent=ua,
                    locale="zh-CN",
                    ignore_https_errors=True,
                    viewport={"width": 1366, "height": 768},
                )
                page = context.new_page()
                _apply_playwright_stealth(context, page)
                doc_bodies: list[bytes] = []

                def on_response(response) -> None:  # type: ignore[no-untyped-def]
                    if response.request.resource_type != "document":
                        return
                    try:
                        body = response.body()
                    except Exception:  # noqa: BLE001
                        return
                    if response.status == 200 and body and len(body) > 500:
                        doc_bodies.append(body)

                page.on("response", on_response)
                try:
                    page.goto(url, wait_until="commit", timeout=timeout * 1000)
                except Exception as exc:  # noqa: BLE001
                    logging.debug("browser goto (%s): %s", label, exc)

                this_wait = first_wait if index == 0 else wait_ms
                deadline = time.time() + max(this_wait / 1000.0, 5.0)
                html = ""
                final_url = url
                blank_ticks = 0
                while time.time() < deadline:
                    try:
                        page.evaluate(
                            "() => { if (document.body) document.body.style.display = 'block'; }"
                        )
                        html = page.content()
                        final_url = page.url
                    except Exception:  # noqa: BLE001
                        html = ""
                    if doc_bodies:
                        candidate = _decode_bytes(max(doc_bodies, key=len))
                        if _html_looks_useful(candidate):
                            html = candidate
                    if _html_looks_useful(html):
                        break
                    if len(html) < 100:
                        blank_ticks += 1
                        # White-screen WAF: don't burn the full wait on empty docs.
                        if blank_ticks >= 6 and not doc_bodies:
                            break
                    page.wait_for_timeout(800)

                if not _html_looks_useful(html) and doc_bodies:
                    html = _decode_bytes(max(doc_bodies, key=len))

                # Fujian 主动公开目录: zTree「人事任免」loads rsxx/rsrm rows into the pane.
                if (
                    _html_looks_useful(html)
                    and "fujian.chinatax.gov.cn" in (final_url or url)
                    and "/zfxxgkml/" in (final_url or url)
                    and "任免工作人员" not in html
                ):
                    try:
                        page.evaluate(
                            """() => {
                              const nodes = [...document.querySelectorAll('a, span, li')];
                              const hit = nodes.find(el =>
                                ((el.innerText || el.textContent || '').trim() === '人事任免'));
                              if (!hit) return false;
                              const a = hit.closest('a') || hit.querySelector('a') || hit;
                              a.click();
                              return true;
                            }"""
                        )
                        click_deadline = time.time() + 12.0
                        while time.time() < click_deadline:
                            page.wait_for_timeout(700)
                            html = page.content()
                            final_url = page.url
                            if "任免工作人员" in html and re.search(
                                r"t20\d{6}_\d+\.htm", html
                            ):
                                break
                    except Exception as exc:  # noqa: BLE001
                        logging.debug("fujian playwright ztree click: %s", exc)

                cookies = context.cookies()
                if _html_looks_useful(html):
                    logging.info("Browser fetch OK via %s (%s bytes)", label, len(html))
                    return final_url, html, cookies

                errors.append(f"{label}: still challenge/empty ({len(html)} bytes)")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
            finally:
                if browser is not None:
                    browser.close()

            # After first Chrome miss, jump to DrissionPage (often wins on Fujian).
            if index == 0:
                try:
                    return _fetch_html_drission(url, timeout=timeout, wait_ms=wait_ms)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"DrissionPage: {exc}")

    try:
        return _fetch_html_drission(url, timeout=timeout, wait_ms=wait_ms)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"DrissionPage: {exc}")

    # Beijing-style Ruishu: Playwright launch stays blank; real Chrome CDP works.
    try:
        return _fetch_html_system_chrome_cdp(url, timeout=timeout, wait_ms=wait_ms)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"system-chrome CDP: {exc}")

    raise RuntimeError(f"Browser fetch failed for {url}\n" + "\n".join(errors))


def fetch_html(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 20,
    referer: str | None = None,
    follow_meta_refresh: bool = True,
    retries: int = 3,
    allow_browser: bool = True,
) -> tuple[str, str]:
    """Return (final_url, html). On HTTP 412 / WAF JS challenge, retry via Playwright."""
    headers = {}
    if referer:
        headers["Referer"] = referer

    errors: list[str] = []
    candidates = [url]
    if url.startswith("https://"):
        candidates.append("http://" + url.removeprefix("https://"))

    html = ""
    final_url = url
    last_error: Exception | None = None
    saw_waf = False

    for attempt in range(retries):
        if attempt:
            time.sleep(1.5 * attempt)
        for candidate in candidates:
            for verify in (True, False):
                try:
                    response = session.get(candidate, timeout=timeout, verify=verify, headers=headers)
                    body = decode_response(response)
                    if response.status_code >= 400 or looks_like_waf_challenge(
                        body, status_code=response.status_code
                    ):
                        saw_waf = response.status_code in {412, 202} or looks_like_waf_challenge(
                            body, status_code=response.status_code
                        )
                        raise RequestException(f"HTTP {response.status_code}")
                    html = body
                    final_url = str(response.url)
                    last_error = None
                    break
                except SSLError as exc:
                    errors.append(f"{candidate} ssl={verify}: {exc}")
                    last_error = exc
                    continue
                except RequestException as exc:
                    errors.append(f"{candidate} ssl={verify}: {exc}")
                    last_error = exc
                    break
            if last_error is None and html:
                break
        if last_error is None and html:
            break

    if (last_error is not None and not html) or looks_like_waf_challenge(html):
        ssl_blocked = any("ssl" in item.lower() or "SSL" in item for item in errors)
        if allow_browser and (
            saw_waf
            or looks_like_waf_challenge(html)
            or _errors_include_412(errors)
            or ssl_blocked
        ):
            logging.info(
                "%s for %s — retrying with Playwright/Drission",
                "WAF/412" if (saw_waf or _errors_include_412(errors)) else "TLS/fetch failure",
                url,
            )
            try:
                final_url, html, cookies = fetch_html_browser(url, timeout=max(timeout, 60))
                apply_browser_cookies(session, cookies)
                last_error = None
            except Exception as exc:  # noqa: BLE001
                errors.append(f"browser: {exc}")
                last_error = exc if last_error is None else last_error
                if not html:
                    raise RuntimeError(f"Failed to fetch {url}\n" + "\n".join(errors)) from exc

    if last_error is not None and not html:
        raise RuntimeError(f"Failed to fetch {url}\n" + "\n".join(errors)) from last_error

    if follow_meta_refresh:
        refresh_url = extract_meta_refresh_url(html, final_url) or extract_js_redirect_url(
            html, final_url
        )
        if refresh_url and refresh_url != final_url:
            return fetch_html(
                session,
                refresh_url,
                timeout=timeout,
                referer=final_url,
                follow_meta_refresh=False,
                retries=retries,
                allow_browser=allow_browser,
            )

    return final_url, html


def _errors_include_412(errors: list[str]) -> bool:
    return any(("HTTP 412" in item or "HTTP 202" in item) for item in errors)


def extract_meta_refresh_url(html: str, base_url: str) -> str | None:
    match = REFRESH_RE.search(html)
    if not match:
        return None
    target = match.group(1).strip().strip("'\"")
    if not target:
        return None
    return urljoin(ensure_trailing_slash(base_url) if "./" in target or target.startswith(".") else base_url, target)


def extract_js_redirect_url(html: str, base_url: str) -> str | None:
    """Follow trivial ``window.location`` redirects used by some column index pages."""
    match = JS_LOCATION_RE.search(html or "")
    if not match:
        return None
    target = (match.group(1) or match.group(2) or "").strip()
    if not target or target.startswith(("javascript:", "data:")):
        return None
    # Tianjin mobile shells: window.location="p"+url.substr(...) — do NOT treat "p" as a redirect.
    tail = (html or "")[match.end() : match.end() + 1]
    if tail == "+":
        return None
    if target in {"p", "m"}:
        return None
    # Ignore redirects that are clearly WAF/challenge loops to the same path.
    return urljoin(
        ensure_trailing_slash(base_url) if "./" in target or target.startswith(".") else base_url,
        target,
    )


def resolve_list_child_url(list_url: str, href: str) -> str:
    """Resolve article href against a list URL that always has a trailing slash."""
    href = (href or "").strip()
    if not href:
        return list_url
    if href.startswith(("http://", "https://")):
        return href
    parsed = urlparse(list_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Site-root absolute path
    if href.startswith("/"):
        return urljoin(origin + "/", href.lstrip("/"))
    # Tianjin-style: list is /u_zlmViewMx.action?... while articles are /11200000000/...
    # urljoin against *.action/ would wrongly produce /u_zlmViewMx.action/11200000000/...
    if ".action" in (parsed.path or "") and re.match(r"^\d{5,}/", href):
        return urljoin(origin + "/", href)
    return urljoin(ensure_trailing_slash(list_url), href)
