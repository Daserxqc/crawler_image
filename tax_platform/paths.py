"""Project-root–anchored paths for portable deploy (Windows / Linux cloud).

Relative paths like ``output/tax_hr.db`` previously depended on the process
cwd. Cron/systemd must still set WorkingDirectory, but defaults now resolve
against the package root so a wrong cwd does not silently create empty DBs.
"""

from __future__ import annotations

import os
from pathlib import Path

# tax_platform/paths.py → repo root
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_project_cwd() -> Path:
    """chdir to repo root (safe to call from CLI entrypoints)."""
    os.chdir(PROJECT_ROOT)
    return PROJECT_ROOT


def output_dir() -> Path:
    env = (os.environ.get("TAX_HR_OUTPUT_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (PROJECT_ROOT / "output").resolve()


def default_db_path() -> Path:
    env = (os.environ.get("TAX_HR_DB") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return output_dir() / "tax_hr.db"


def resolve_data_path(path: str | Path | None, *, default_name: str) -> Path:
    """Resolve a user/CLI path; relative paths are under PROJECT_ROOT."""
    if path is None or str(path).strip() == "":
        return output_dir() / default_name
    raw = Path(str(path)).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    return (PROJECT_ROOT / raw).resolve()


def under_output(*parts: str) -> Path:
    return output_dir().joinpath(*parts)
