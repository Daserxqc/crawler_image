"""Provinces covered by manual HTML ingest — skip auto-crawl when folder exists."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# City-level bureaus ingested from output/manual/{code}/ instead of live crawl.
MANUAL_CITY_PROVINCES = frozenset(
    {
        "beijing",
        "guangdong",
        "hainan",
        "hebei",
        "heilongjiang",
        "henan",
        "jilin",
        "liaoning",
        "neimenggu",
        "shanxi",
        "sichuan",
        "xinjiang",
    }
)


def manual_folder(parent_code: str) -> Path:
    return ROOT / "output" / "manual" / parent_code


def has_manual_data(parent_code: str) -> bool:
    folder = manual_folder(parent_code)
    if not folder.is_dir():
        return False
    if (folder / "ingest_report.json").exists():
        return True
    if any(folder.glob("*.html")):
        return True
    if any(folder.glob("*/manifest.json")):
        return True
    return False


def should_skip_auto_crawl(parent_code: str) -> bool:
    """Skip province/city auto-crawl when manual ingest folder is populated."""
    if parent_code not in MANUAL_CITY_PROVINCES:
        return False
    return has_manual_data(parent_code)


def filter_parents(parent_codes: list[str]) -> tuple[list[str], list[str]]:
    """Return (kept, skipped) parent province codes."""
    kept: list[str] = []
    skipped: list[str] = []
    for code in parent_codes:
        if should_skip_auto_crawl(code):
            skipped.append(code)
        else:
            kept.append(code)
    return kept, skipped
