from __future__ import annotations

import re
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


def fetch_html(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 20,
    referer: str | None = None,
    follow_meta_refresh: bool = True,
) -> tuple[str, str]:
    """Return (final_url, html). Optionally follow one META REFRESH hop (leader intro hubs)."""
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

    for candidate in candidates:
        for verify in (True, False):
            try:
                response = session.get(candidate, timeout=timeout, verify=verify, headers=headers)
                if response.status_code >= 400:
                    raise RequestException(f"HTTP {response.status_code}")
                html = decode_response(response)
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
            )

    return final_url, html


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
    return urljoin(ensure_trailing_slash(list_url), href)
