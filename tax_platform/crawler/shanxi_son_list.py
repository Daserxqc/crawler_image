"""Click 人事任免 on Shanxi son/list shells when wzList is still empty."""
from __future__ import annotations

import logging
import re
import time
from typing import Any


def _drission_try_shanxi_son_list(page: Any, shell_html: str) -> str | None:
    if "/son/list/" not in (page.url or "") and "/son/list/" not in (shell_html or ""):
        return None
    if "son/detail/" in (shell_html or "") and "任免" in (shell_html or ""):
        return shell_html
    try:
        clicked = page.run_js(
            """
            const hit = [...document.querySelectorAll('a, li a, div[onclick*="showMx"]')]
              .find(el => ((el.innerText || el.textContent || '').includes('人事任免')));
            if (!hit) return false;
            hit.click();
            return true;
            """
        )
        if not clicked:
            return None
    except Exception as exc:  # noqa: BLE001
        logging.debug("shanxi son/list click failed: %s", exc)
        return None
    deadline = time.time() + 15.0
    html = shell_html or ""
    while time.time() < deadline:
        time.sleep(0.6)
        html = page.html or ""
        if "son/detail/" in html and "任免" in html:
            return html
        if re.search(r'son/detail/[^"\']+', html) and 'id="wzList"' in html:
            return html
    return html if "son/detail/" in html else None
