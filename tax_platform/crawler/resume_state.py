"""Persist per-job ingest/crawl progress for resume after interrupt."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tax_platform.paths import under_output

DEFAULT_RESUME_PATH = under_output("_resume_state.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_resume_state(path: str | Path = DEFAULT_RESUME_PATH) -> dict[str, Any]:
    resume_path = Path(path)
    if not resume_path.exists():
        return {"jobs": {}, "updated_at": None}
    return json.loads(resume_path.read_text(encoding="utf-8"))


def save_resume_state(state: dict[str, Any], path: str | Path = DEFAULT_RESUME_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now_iso()
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return out.resolve()


def job_completed_codes(state: dict[str, Any], job_id: str) -> set[str]:
    job = state.get("jobs", {}).get(job_id, {})
    return set(job.get("completed_codes", []))


def is_city_done(state: dict[str, Any], job_id: str, code: str, *, resume: bool) -> bool:
    if not resume:
        return False
    return code in job_completed_codes(state, job_id)


def mark_city_done(
    state: dict[str, Any],
    job_id: str,
    code: str,
    *,
    stats: dict[str, Any] | None = None,
    path: str | Path = DEFAULT_RESUME_PATH,
) -> None:
    jobs = state.setdefault("jobs", {})
    job = jobs.setdefault(
        job_id,
        {"completed_codes": [], "last_code": None, "cities": {}},
    )
    completed = job.setdefault("completed_codes", [])
    if code not in completed:
        completed.append(code)
    job["last_code"] = code
    if stats:
        job.setdefault("cities", {})[code] = {**stats, "completed_at": _now_iso()}
    save_resume_state(state, path)


def mark_job_stopped(
    state: dict[str, Any],
    job_id: str,
    *,
    reason: str = "interrupted",
    path: str | Path = DEFAULT_RESUME_PATH,
) -> None:
    jobs = state.setdefault("jobs", {})
    job = jobs.setdefault(job_id, {})
    job["stopped_at"] = _now_iso()
    job["stop_reason"] = reason
    save_resume_state(state, path)
