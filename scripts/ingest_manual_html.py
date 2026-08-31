# -*- coding: utf-8 -*-
"""Ingest manually saved HTML under output/manual/."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_job import leaders_payload
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.config.sites import get_site, reload_sites
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL_ROOT = ROOT / "output" / "manual"
_FLAT_APPT = re.compile(r"^(.+?)_appt_list\.html?$", re.I)
_FLAT_LEADER = re.compile(r"^(.+?)_leader\.html?$", re.I)


def _read_html(path: Path) -> str:
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _collect_sites() -> dict[str, dict[str, Path]]:
    """Return {bureau_code: {kind: path}}."""
    found: dict[str, dict[str, Path]] = {}

    if not MANUAL_ROOT.exists():
        return found

    for child in sorted(MANUAL_ROOT.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            code = child.name
            bucket = found.setdefault(code, {})
            appt = child / "appt_list.html"
            if not appt.exists():
                appt = child / "appt_list.htm"
            if appt.exists():
                bucket["appt_list"] = appt
            leader = child / "leader.html"
            if not leader.exists():
                leader = child / "leader.htm"
            if leader.exists():
                bucket["leader"] = leader
            notices = child / "notices"
            if notices.is_dir():
                bucket["notices_dir"] = notices
            continue

        if child.suffix.lower() not in {".html", ".htm"}:
            continue
        m = _FLAT_APPT.match(child.name)
        if m:
            found.setdefault(m.group(1), {})["appt_list"] = child
            continue
        m = _FLAT_LEADER.match(child.name)
        if m:
            found.setdefault(m.group(1), {})["leader"] = child

    return found


def _ingest_appt(code: str, appt_path: Path, notices_dir: Path | None) -> AppointmentCrawlResult:
    site = get_site(code)
    html = _read_html(appt_path)
    base_url = site.appointment_list_url or f"file:///{appt_path.as_posix()}"
    list_url = base_url
    items = parse_appointment_list(html, list_url)
    notices = []
    events = []
    failed = []

    detail_files = []
    if notices_dir and notices_dir.is_dir():
        detail_files = sorted(notices_dir.glob("*.html")) + sorted(notices_dir.glob("*.htm"))

    # Try matching list items to saved detail files
    used_files: set[Path] = set()
    for item in items:
        detail_html = None
        detail_url = item.source_url
        for fp in detail_files:
            if fp in used_files:
                continue
            # Heuristic: if only one unused file left or title snippet in filename
            slug = re.sub(r"\W+", "", item.title or "")[:12]
            if slug and slug in re.sub(r"\W+", "", fp.stem):
                detail_html = _read_html(fp)
                detail_url = f"file:///{fp.as_posix()}"
                used_files.add(fp)
                break
        if detail_html is None and detail_files:
            for fp in detail_files:
                if fp not in used_files:
                    detail_html = _read_html(fp)
                    detail_url = f"file:///{fp.as_posix()}"
                    used_files.add(fp)
                    break
        if detail_html is None:
            # List-only: skip unless we can parse inline (rare)
            continue
        try:
            notice = parse_appointment_detail(detail_html, detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": detail_url, "error": str(exc)})

    # Orphan detail files not matched from list
    for fp in detail_files:
        if fp in used_files:
            continue
        detail_url = f"file:///{fp.as_posix()}"
        try:
            notice = parse_appointment_detail(_read_html(fp), detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": detail_url, "error": str(exc)})

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )


def _ingest_leader(code: str, leader_path: Path) -> LeaderCrawlResult:
    site = get_site(code)
    html = _read_html(leader_path)
    hub_url = site.leader_intro_url or f"file:///{leader_path.as_posix()}"
    pages = [hub_url]
    for target in leader_page_targets(html, hub_url):
        if target not in pages:
            pages.append(target)

    leaders_by_name: dict = {}
    for duty in parse_leader_intro(html, hub_url, code):
        leaders_by_name[duty.person_name] = duty

    return LeaderCrawlResult(
        bureau=code,
        hub_url=hub_url,
        page_count=len(pages),
        leaders=list(leaders_by_name.values()),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reload_sites()
    sites = _collect_sites()
    if not sites:
        logging.info("No HTML under %s — see output/manual/README.md", MANUAL_ROOT)
        return

    appt_results = []
    lead_results = []
    for code, paths in sorted(sites.items()):
        try:
            get_site(code)
        except KeyError:
            logging.warning("Unknown bureau code %s — add to registry or rename folder", code)
            continue
        if "appt_list" in paths:
            r = _ingest_appt(code, paths["appt_list"], paths.get("notices_dir"))
            appt_results.append(r)
            logging.info(
                "APPT %s list=%s notices=%s events=%s failed=%s",
                code,
                r.list_count,
                len(r.notices),
                len(r.events),
                len(r.failed),
            )
        if "leader" in paths:
            r = _ingest_leader(code, paths["leader"])
            lead_results.append(r)
            logging.info("LEAD %s leaders=%s", code, len(r.leaders))

    if args.dry_run:
        logging.info("dry-run: would ingest %s appt / %s lead sites", len(appt_results), len(lead_results))
        return

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/manual_ingest_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/manual_ingest_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
        if lead_payload:
            logging.info("ingest lead %s", ingest_leader_results(lead_payload, conn=conn))
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
