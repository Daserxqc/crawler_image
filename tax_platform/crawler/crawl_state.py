"""Persist last crawl times and decide which sites are due."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tax_platform.config.schedule import refresh_days_for
from tax_platform.config.sites import BureauSite, list_sites
from tax_platform.paths import under_output

DEFAULT_STATE_PATH = under_output("crawl_state.json")


@dataclass(frozen=True)
class CrawlRunRecord:
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    ok: bool | None = None


def state_key(kind: str, site_code: str) -> str:
    return f"{kind}:{site_code}"


def load_crawl_state(path: str | Path = DEFAULT_STATE_PATH) -> dict[str, CrawlRunRecord]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    records: dict[str, CrawlRunRecord] = {}
    for key, value in raw.items():
        records[key] = CrawlRunRecord(
            last_success_at=_parse_dt(value.get("last_success_at")),
            last_attempt_at=_parse_dt(value.get("last_attempt_at")),
            ok=value.get("ok"),
        )
    return records


def save_crawl_state(records: dict[str, CrawlRunRecord], path: str | Path = DEFAULT_STATE_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: {
            "last_success_at": record.last_success_at.isoformat() if record.last_success_at else None,
            "last_attempt_at": record.last_attempt_at.isoformat() if record.last_attempt_at else None,
            "ok": record.ok,
        }
        for key, record in sorted(records.items())
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out.resolve()


def mark_crawl_result(
    records: dict[str, CrawlRunRecord],
    *,
    kind: str,
    site_code: str,
    ok: bool,
    when: datetime | None = None,
) -> None:
    now = when or datetime.now(timezone.utc)
    previous = records.get(state_key(kind, site_code), CrawlRunRecord())
    records[state_key(kind, site_code)] = CrawlRunRecord(
        last_success_at=now if ok else previous.last_success_at,
        last_attempt_at=now,
        ok=ok,
    )


def is_site_due(
    site: BureauSite,
    *,
    kind: str,
    records: dict[str, CrawlRunRecord],
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    record = records.get(state_key(kind, site.code))
    if record is None or record.last_success_at is None:
        return True
    interval = timedelta(days=refresh_days_for(site.level, site.refresh_days))
    last = record.last_success_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return current - last >= interval


def sites_due_for_crawl(
    kind: str,
    *,
    records: dict[str, CrawlRunRecord] | None = None,
    level: str | None = None,
    now: datetime | None = None,
) -> list[BureauSite]:
    state = records if records is not None else load_crawl_state()
    return [
        site
        for site in list_sites(level)
        if is_site_due(site, kind=kind, records=state, now=now)
    ]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
