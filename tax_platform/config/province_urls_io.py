from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SITES_FILE = Path(__file__).resolve().parent / "sites_provinces.py"
DEFAULT_REGISTRY = Path("output/province_urls.json")


def load_discovery_registry(path: Path = DEFAULT_REGISTRY) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge_discovery_entry(entry: dict[str, Any], *, path: Path = DEFAULT_REGISTRY) -> list[dict[str, Any]]:
    by_code = {item["code"]: item for item in load_discovery_registry(path)}
    by_code[entry["code"]] = entry
    merged = [by_code[code] for code in sorted(by_code)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def load_current_overrides() -> dict[str, dict[str, str]]:
    from tax_platform.config.sites_provinces import PROVINCE_URL_OVERRIDES

    return dict(PROVINCE_URL_OVERRIDES)


def entry_to_override(entry: dict[str, Any]) -> dict[str, str] | None:
    override: dict[str, str] = {}
    if entry.get("home_url"):
        override["home_url"] = entry["home_url"]
    if entry.get("leader_intro_url"):
        override["leader_intro_url"] = entry["leader_intro_url"]
    if entry.get("appointment_list_url"):
        override["appointment_list_url"] = entry["appointment_list_url"]
    return override or None


def merge_override(code: str, override: dict[str, str]) -> dict[str, dict[str, str]]:
    merged = load_current_overrides()
    merged[code] = {**merged.get(code, {}), **override}
    return merged


def write_overrides(overrides: dict[str, dict[str, str]]) -> None:
    text = SITES_FILE.read_text(encoding="utf-8")
    block = "PROVINCE_URL_OVERRIDES: dict[str, dict[str, str]] = " + json.dumps(
        overrides,
        ensure_ascii=False,
        indent=4,
    )
    new_text, count = re.subn(
        r"PROVINCE_URL_OVERRIDES: dict\[str, dict\[str, str\]\] = \{[\s\S]*?\n\}",
        block,
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not locate PROVINCE_URL_OVERRIDES block in sites_provinces.py")
    SITES_FILE.write_text(new_text, encoding="utf-8")
