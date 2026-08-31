# -*- coding: utf-8 -*-
"""Archive + ingest Sichuan city manual HTML from output/manual/sichuan/."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult, appointments_payload
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.crawler.ingest_resume import JOB_SICHUAN, ingest_and_checkpoint_city, should_skip_city
from tax_platform.crawler.resume_state import load_resume_state
from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "sichuan"
ARCHIVE = MANUAL
BASE = "https://sichuan.chinatax.gov.cn"
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)
ART_RE = re.compile(r"/art/\d+/", re.I)
HIDDEN_RE = re.compile(r'id="(?P<key>[^"]+)"[^>]*>(?P<val>[^<]+)')
CITY_RE = re.compile(r"(?:国家税务总局(?:四川省)?|四川省)(.+?)税务局")

# Leader col IDs — cities with no appointment column on site.
NO_APPT_CODES: frozenset[str] = frozenset(
    {
        "sichuan_col1153",  # 德阳市
        "sichuan_col1183",  # 绵阳市
        "sichuan_col1363",  # 宜宾市
        "sichuan_col1423",  # 达州市
        "sichuan_col1513",  # 眉山市
        "sichuan_col1545",  # 资阳市
        "sichuan_col1635",  # 凉山州
    }
)

CITY_ALIASES = {
    "凉山市": "凉山州",
    "甘孜市": "甘孜州",
}


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    best: tuple[int, str] | None = None
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = len(re.findall(r"[\u4e00-\u9fa5]", text)) - text.count("\ufffd") * 50
        if best is None or score > best[0]:
            best = (score, text)
    return best[1] if best else raw.decode("utf-8", errors="replace")


def _hidden_map(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in HIDDEN_RE.finditer(html):
        out[m.group("key")] = m.group("val").strip()
    return out


def _hidden(html: str, key: str) -> str:
    return _hidden_map(html).get(key, "")


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _col_id_from_url(url: str) -> str:
    m = COL_CODE_RE.search(url or "")
    return m.group(1) if m else ""


def _city_from_filename(filename: str) -> str:
    m = CITY_RE.search(filename)
    if not m:
        return ""
    city = m.group(1).strip()
    city = CITY_ALIASES.get(city, city)
    return city


def _code_from_col(col_id: str) -> str:
    return f"sichuan_col{col_id}" if col_id else ""


def _bureau_name(city: str) -> str:
    if not city:
        return ""
    if city.endswith(("州", "市", "区", "县")):
        return f"国家税务总局{city}税务局"
    return f"国家税务总局{city}市税务局"


def _is_appt_file(html: str, filename: str) -> bool:
    col1 = _hidden(html, "col1_name")
    if col1 in ("人事信息", "人事任免"):
        return True
    if ("人事信息" in filename or "人事任免" in filename) and "领导" not in filename:
        return True
    return False


def _is_leader_file(html: str, filename: str) -> bool:
    if "领导简介" in filename or "领导专栏" in filename:
        return True
    return _hidden(html, "col1_name") in ("领导简介", "领导专栏")


def _is_appt_detail_file(html: str, filename: str) -> bool:
    if _is_appt_file(html, filename) or _is_leader_file(html, filename):
        return False
    url = _saved_url(html, "")
    if ART_RE.search(url):
        return True
    if "任免" in filename or "任命" in filename or "免职" in filename:
        return True
    return False


def _abs_url(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"{BASE}{path if path.startswith('/') else '/' + path}"


def _duty_richness(duty: LeaderDuty) -> tuple[int, int, int]:
    deps = duty.departments_raw or []
    return (len(deps), 1 if (duty.duty_summary or "").strip() else 0, len(duty.title_raw or ""))


def _merge_duties(*groups: list[LeaderDuty]) -> list[LeaderDuty]:
    by_name: dict[str, LeaderDuty] = {}
    for group in groups:
        for duty in group:
            if not is_plausible_person_name(duty.person_name):
                continue
            prev = by_name.get(duty.person_name)
            if prev is None or _duty_richness(duty) > _duty_richness(prev):
                by_name[duty.person_name] = duty
    return list(by_name.values())


def _classify_files() -> dict[str, dict]:
    by_city: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)

    registry = {
        e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("sichuan_col")
    }

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            city = _city_from_filename(path.name)
            if not city:
                logging.warning("skip unclassified %s", path.name)
                continue

            saved = _saved_url(html, "")
            loc = _hidden_map(html)
            col5_url = _abs_url(loc.get("col5_url", ""))
            col1_url = _abs_url(loc.get("col1_url", ""))
            col5_name = loc.get("col5_name") or _bureau_name(city)

            bucket = by_city.setdefault(
                city,
                {
                    "city": city,
                    "code": "",
                    "name": registry.get("", {}).get("name") or _bureau_name(city),
                    "leader_files": [],
                    "appt_list_html": None,
                    "appt_detail_files": [],
                    "leader_url": "",
                    "appt_url": "",
                    "home_url": col5_url,
                },
            )
            if col5_name:
                bucket["name"] = col5_name if col5_name.startswith("国家税务总局") else f"国家税务总局{col5_name}"
            if col5_url:
                bucket["home_url"] = col5_url

            if _is_appt_file(html, path.name):
                bucket["appt_list_html"] = path
                if col1_url:
                    bucket["appt_url"] = col1_url
                elif saved:
                    bucket["appt_url"] = saved
                appt_col = _col_id_from_url(saved)
                if appt_col and not bucket.get("appt_col"):
                    bucket["appt_col"] = appt_col
            elif _is_leader_file(html, path.name):
                bucket["leader_files"].append(path)
                if col1_url:
                    bucket["leader_url"] = col1_url
                elif saved:
                    bucket["leader_url"] = saved
                leader_col = _col_id_from_url(saved)
                if leader_col:
                    bucket["leader_col"] = leader_col
                    bucket["code"] = _code_from_col(leader_col)
            elif _is_appt_detail_file(html, path.name):
                bucket["appt_detail_files"].append(path)
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, city)

    groups: dict[str, dict] = {}
    for city, bucket in by_city.items():
        code = bucket.get("code") or _code_from_col(bucket.get("appt_col", ""))
        if not code:
            logging.warning("skip city without code: %s", city)
            continue
        reg = registry.get(code, {})
        bucket["code"] = code
        if reg.get("name"):
            bucket["name"] = reg["name"]
        if reg.get("leader_intro_url"):
            bucket["leader_url"] = reg["leader_intro_url"]
        if reg.get("appointment_list_url"):
            bucket["appt_url"] = reg["appointment_list_url"]
        if reg.get("home_url"):
            bucket["home_url"] = reg["home_url"]
        groups[code] = bucket
    return groups


def _registry_entries(groups: dict[str, dict]) -> list[dict]:
    entries = []
    for code, meta in sorted(groups.items()):
        home = meta.get("home_url") or ""
        leader = meta.get("leader_url") or ""
        appt = meta.get("appt_url") or ""
        notes = ["sichuan_manual"]
        if code in NO_APPT_CODES:
            notes.append("no_appointment_column")
        entries.append(
            {
                "code": code,
                "name": meta["name"],
                "home_url": home,
                "leader_intro_url": leader,
                "appointment_list_url": appt if code not in NO_APPT_CODES else "",
                "level": "city",
                "parent_code": "sichuan",
                "region": "四川省",
                "notes": notes,
            }
        )
    return entries


def _patch_registry(groups: dict[str, dict]) -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for entry in _registry_entries(groups):
        code = entry["code"]
        merged = {**by_code.get(code, {}), **entry}
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    reload_sites()
    return updated


def _archive_group(code: str, meta: dict) -> Path:
    dest = ARCHIVE / code
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"code": code, "name": meta["name"], "files": []}

    leaders_dir = dest / "leaders"
    leaders_dir.mkdir(exist_ok=True)
    archived_leaders: list[Path] = []
    for i, src in enumerate(meta.get("leader_files") or []):
        target = leaders_dir / f"leader_{i}.html"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        archived_leaders.append(target)
        manifest["files"].append(f"leaders/leader_{i}.html")

    appt_archived = None
    if meta.get("appt_list_html"):
        appt_archived = dest / "appt_list.html"
        src = meta["appt_list_html"]
        if src.resolve() != appt_archived.resolve():
            shutil.copy2(src, appt_archived)
        manifest["files"].append("appt_list.html")

    notices_dir = dest / "notices"
    notices_dir.mkdir(exist_ok=True)
    archived_details: list[Path] = []
    for i, src in enumerate(meta.get("appt_detail_files") or []):
        target = notices_dir / f"manual_{i}.html"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        archived_details.append(target)
        manifest["files"].append(f"notices/manual_{i}.html")

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
    meta["archived_appt_details"] = archived_details
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_files") or [])
        if meta.get("appt_list_html"):
            paths.add(meta["appt_list_html"])
        paths.update(meta.get("appt_detail_files") or [])
    for path in sorted(paths):
        if not path.exists() or path.parent != MANUAL:
            continue
        dest = source_dir / path.name
        if dest.resolve() != path.resolve():
            shutil.move(str(path), str(dest))
        asset_dir = MANUAL / f"{path.stem}_files"
        if asset_dir.is_dir():
            asset_dest = source_dir / asset_dir.name
            if asset_dest.exists():
                shutil.rmtree(asset_dest, ignore_errors=True)
            shutil.move(str(asset_dir), str(asset_dest))


def _parse_leaders(code: str, paths: list[Path], *, try_fetch: bool) -> tuple[LeaderCrawlResult, list[str]]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))

    missing_bio = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
    ]

    if try_fetch and missing_bio and paths:
        session = create_session()
        by_name = {d.person_name: d for d in all_duties}
        seen_urls: set[str] = set()
        for path in paths:
            html = _read_html(path)
            hub = _saved_url(html, f"file:///{path.as_posix()}")
            for url in leader_page_targets(html, hub):
                key = url.split("?", 1)[0].rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                if not ART_RE.search(url):
                    continue
                try:
                    time.sleep(0.25)
                    final, detail = fetch_html(session, url, referer=hub, follow_meta_refresh=False)
                    for duty in parse_leader_intro(detail, final, code):
                        prev = by_name.get(duty.person_name)
                        if prev is None or _duty_richness(duty) > _duty_richness(prev):
                            by_name[duty.person_name] = duty
                except Exception as exc:  # noqa: BLE001
                    logging.debug("leader fetch skip %s: %s", url, exc)
        session.close()
        all_duties = list(by_name.values())
        missing_bio = [
            d.person_name
            for d in all_duties
            if is_plausible_person_name(d.person_name) and not (d.title_raw or d.duty_summary)
        ]

    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(paths), leaders=all_duties),
        missing_bio,
    )


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    notices = []
    events = []
    failed = []
    list_url = ""
    list_count = 0

    detail_paths = list(meta.get("archived_appt_details") or [])
    for path in detail_paths:
        html = _read_html(path)
        detail_url = _saved_url(html, f"file:///{path.as_posix()}")
        if detail_url in known:
            continue
        try:
            notice = parse_appointment_detail(html, detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": detail_url, "error": str(exc)})

    path = meta.get("archived_appt_list")
    if path is None or not path.exists():
        return AppointmentCrawlResult(
            bureau=code,
            list_url=list_url,
            list_count=list_count,
            notices=notices,
            events=events,
            failed=failed,
        )

    html = _read_html(path)
    list_url = _saved_url(html, f"file:///{path.as_posix()}")
    items = parse_appointment_list(html, list_url)
    list_count = len(items)
    session = create_session() if try_fetch else None
    notices_dir = ARCHIVE / code / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in items:
            if item.source_url in known:
                continue
            if not try_fetch or session is None:
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1].split("?")[0])[:80]
            cache_path = notices_dir / f"fetch_{slug}.html"
            try:
                time.sleep(0.2)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(
                        session,
                        item.source_url,
                        referer=list_url,
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": item.source_url, "error": str(exc)})
    finally:
        if session:
            session.close()

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=list_count,
        notices=notices,
        events=events,
        failed=failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip cities already checkpointed in output/_resume_state.json",
    )
    parser.add_argument("--codes", nargs="+", metavar="CODE")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    if args.codes:
        only = frozenset(args.codes)
        groups = {k: v for k, v in groups.items() if k in only}
    logging.info("classified %s Sichuan cities", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report: dict = {
        "cities": [],
        "stats": {},
        "leader_missing_bio": {},
        "appt_failed": {},
        "no_appt": sorted(NO_APPT_CODES),
    }

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "city": meta.get("city", ""),
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": code not in NO_APPT_CODES
                and bool(meta.get("appt_list_html") or meta.get("appt_detail_files")),
                "no_appt": code in NO_APPT_CODES,
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    resume_state = load_resume_state()
    appt_results = []
    lead_results = []
    for code, meta in sorted(groups.items()):
        if should_skip_city(resume_state, JOB_SICHUAN, code, resume=args.resume):
            continue
        leaders = meta.get("archived_leaders") or []
        lead_n = appt_list_n = appt_events_n = 0
        lr = None
        ar = None
        if leaders:
            lr, missing = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
            lead_results.append(lr)
            lead_n = len(lr.leaders)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio)", code, lead_n, len(missing))
        if code not in NO_APPT_CODES:
            ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            appt_list_n = ar.list_count
            appt_events_n = len(ar.events)
            if ar.list_count or ar.notices:
                appt_results.append(ar)
            if ar.failed:
                report["appt_failed"][code] = ar.failed[:10]
            logging.info(
                "APPT %s list=%s notices=%s events=%s failed=%s",
                code,
                ar.list_count,
                len(ar.notices),
                len(ar.events),
                len(ar.failed),
            )
        city_stats = {
            "name": meta["name"],
            "city": meta.get("city", ""),
            "leaders": lead_n,
            "appt_list": appt_list_n,
            "appt_events": appt_events_n,
            "no_appt": code in NO_APPT_CODES,
        }
        report["stats"][code] = city_stats
        ingest_and_checkpoint_city(
            code,
            lr,
            ar,
            db=args.db,
            job_id=JOB_SICHUAN,
            resume_state=resume_state,
            stats=city_stats,
        )

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/sichuan_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/sichuan_manual_leaders.json", lead_payload)

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
