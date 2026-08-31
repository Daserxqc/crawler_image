"""Shared per-city commit + resume skip for manual ingest scripts."""

from __future__ import annotations

import logging
from typing import Any

from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.crawler.resume_state import is_city_done, mark_city_done
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

JOB_SICHUAN = "ingest_sichuan_manual"
JOB_HEBEI = "ingest_hebei_manual"
JOB_XINJIANG = "ingest_xinjiang_manual"
JOB_JILIN = "ingest_jilin_manual"
JOB_NEIMENGGU = "ingest_neimenggu_manual"
JOB_GUANGDONG = "ingest_guangdong_manual"
JOB_SHANXI = "ingest_shanxi_manual"


def ingest_and_checkpoint_city(
    code: str,
    lr: LeaderCrawlResult | None,
    ar: AppointmentCrawlResult | None,
    *,
    db: str,
    job_id: str,
    resume_state: dict[str, Any],
    stats: dict[str, Any],
) -> None:
    conn = connect(db)
    try:
        if ar is not None and (ar.list_count or ar.notices):
            ingest_appointment_results(appointments_payload([ar]), conn=conn)
        if lr is not None and lr.leaders:
            ingest_leader_results(leaders_payload([lr]), conn=conn)
        n = recompute_persons(conn)
        conn.commit()
        logging.info("ingest+commit %s (persons=%s)", code, n)
    finally:
        conn.close()
    mark_city_done(resume_state, job_id, code, stats=stats)


def should_skip_city(resume_state: dict[str, Any], job_id: str, code: str, *, resume: bool) -> bool:
    if is_city_done(resume_state, job_id, code, resume=resume):
        logging.info("SKIP %s (already in resume checkpoint)", code)
        return True
    return False
