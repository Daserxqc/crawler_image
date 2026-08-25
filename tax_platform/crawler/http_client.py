from __future__ import annotations

import logging
import re
import time
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
    if any(marker in html for marker in _REAL_CONTENT_MARKERS) and len(html) >= 2_500:
        return True
    return len(html) > 12_000 and "$_ts" not in html[:3000]


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


def _fetch_html_drission(url: str, *, timeout: int = 60, wait_ms: int = 20_000) -> tuple[str, str, list[dict[str, Any]]]:
    """Optional Ruishu-oriented fallback using system Chrome via DrissionPage."""
    from DrissionPage import ChromiumOptions, ChromiumPage

    options = ChromiumOptions()
    # Never pop a visible browser window during crawls.
    options.headless(True)
    options.set_argument("--disable-blink-features=AutomationControlled")
    options.set_argument("--headless=new")
    options.set_argument("--window-size=1920,1080")
    options.set_user_agent(DEFAULT_HEADERS["User-Agent"])
    page = ChromiumPage(options)
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
                break
            time.sleep(0.8)
        cookies_raw = page.cookies(all_domains=True) or []
        cookies: list[dict[str, Any]] = []
        for item in cookies_raw:
            if isinstance(item, dict) and item.get("name"):
                cookies.append(item)
        if not _html_looks_useful(html):
            raise RuntimeError(f"DrissionPage still challenge/empty ({len(html)} bytes)")
        logging.info("Browser fetch OK via DrissionPage (%s bytes)", len(html))
        return page.url or url, html, cookies
    finally:
        try:
            page.quit()
        except Exception:  # noqa: BLE001
            pass


def fetch_html_browser(
    url: str,
    *,
    timeout: int = 60,
    wait_ms: int = 25_000,
    headless: bool | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Fetch URL with a headless browser so Ruishu/412 JS challenges can complete.

    Returns ``(final_url, html, cookies)``.

    Always headless (no visible windows). Blank pages mean WAF detected
    automation — not a broken Playwright install. Tries Chrome channel + stealth,
    Firefox, bundled Chromium, then DrissionPage.
    """
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
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--headless=new",
                ],
            },
        ),
        ("firefox", {"headless": use_headless}),
        (
            "chromium",
            {
                "headless": use_headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--headless=new",
                ],
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
        refresh_url = extract_meta_refresh_url(html, final_url)
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
