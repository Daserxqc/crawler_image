# -*- coding: utf-8 -*-
"""Archive + ingest Jilin city manual HTML from output/manual/jilin/."""

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
from tax_platform.crawler.ingest_resume import ingest_and_checkpoint_city, should_skip_city
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.crawler.resume_state import load_resume_state
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "jilin"
ARCHIVE = MANUAL
BASE = "https://jilin.chinatax.gov.cn"
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)
ART_PATH_RE = re.compile(r"/art/\d{4}/\d{1,2}/\d{1,2}/art_(\d+)_(\d+)\.html", re.I)
HIDDEN_RE = re.compile(r'id="(?P<key>[^"]+)"[^>]*>(?P<val>[^<]*)')
JOB_ID = "ingest_jilin_manual"

# 四平、辽源无人事任免栏目
NO_APPT_CODES: frozenset[str] = frozenset({"jilin_col824", "jilin_col841"})

CITY_SUFFIXES = (
    "梅河新区",
    "长春市",
    "吉林市",
    "四平市",
    "辽源市",
    "通化市",
    "白山市",
    "白城市",
    "松原市",
    "延边州",
)


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
    return {m.group("key"): m.group("val").strip() for m in HIDDEN_RE.finditer(html)}


def _saved_url(html: str, fallback: str = "") -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _abs_url(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"{BASE}{path if path.startswith('/') else '/' + path}"


def _bureau_name(label: str) -> str:
    name = (label or "").strip()
    if not name:
        return ""
    if name.startswith("国家税务总局"):
        return name
    if name.endswith("税务局"):
        return f"国家税务总局{name}"
    return f"国家税务总局吉林省{name}税务局"


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    loc = _hidden_map(html)
    for name_key, url_key in (
        ("col4_name", "col4_url"),
        ("col5_name", "col5_url"),
        ("col3_name", "col3_url"),
        ("col2_name", "col2_url"),
    ):
        name = loc.get(name_key, "")
        url = loc.get(url_key, "")
        if not name or "税务局" not in name:
            continue
        if name in {"市州局站点", "信息公开"}:
            continue
        m = COL_CODE_RE.search(url)
        if not m:
            continue
        return f"jilin_col{m.group(1)}", _bureau_name(name)

    for city in CITY_SUFFIXES:
        if city in filename:
            return "", _bureau_name(f"{city}税务局" if not city.endswith("税务局") else city)
    return "", ""


def _is_appt_file(html: str, filename: str) -> bool:
    col1 = _hidden_map(html).get("col1_name", "")
    if col1 in ("人事信息", "人事任免", "法定主动公开内容"):
        return True
    if "法定主动公开" in filename and "领导" not in filename:
        return True
    return False


def _is_leader_detail_file(html: str, filename: str) -> bool:
    if "领导简介" in filename or "领导专栏" in filename:
        return True
    loc = _hidden_map(html)
    if loc.get("col1_name") in ("领导简介", "领导专栏"):
        return bool(ART_PATH_RE.search(_saved_url(html, "")))
    return False


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


def _jilin_leader_detail_urls(html: str) -> list[str]:
    """Extract sibling leader art links from Jilin ldList sidebar."""
    saved = _saved_url(html, "")
    col_id = ""
    m = re.search(r"/art_(\d+)_\d+\.html", saved)
    if m:
        col_id = m.group(1)
    if not col_id:
        loc = _hidden_map(html)
        cm = COL_CODE_RE.search(loc.get("col1_url", ""))
        if cm:
            col_id = cm.group(1)
    urls: list[str] = []
    seen: set[str] = set()
    for m in ART_PATH_RE.finditer(html):
        art_col = m.group(1)
        if col_id and art_col != col_id:
            continue
        path = m.group(0)
        url = f"{BASE}{path}"
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        urls.append(key)
    return urls


def _classify_files() -> dict[str, dict]:
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            code, bureau_name = _code_from_html(html, path.name)
            if not code:
                logging.warning("skip unclassified %s (%s)", path.name, bureau_name)
                continue
            loc = _hidden_map(html)
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "name": bureau_name,
                    "leader_detail_files": [],
                    "appt_list_html": None,
                    "leader_url": "",
                    "appt_url": "",
                    "home_url": "",
                },
            )
            if bureau_name and (not bucket["name"] or len(bureau_name) > len(bucket["name"])):
                bucket["name"] = bureau_name

            home = _abs_url(loc.get("col4_url") or loc.get("col5_url") or "")
            if home and "/col/col" in home:
                bucket["home_url"] = home.split("?")[0]

            if _is_appt_file(html, path.name):
                bucket["appt_list_html"] = path
                appt_url = _abs_url(loc.get("col1_url") or "")
                if appt_url:
                    bucket["appt_url"] = appt_url.replace("&amp;", "&")
            elif _is_leader_detail_file(html, path.name):
                bucket["leader_detail_files"].append(path)
                leader_url = _abs_url(loc.get("col1_url") or "")
                if leader_url:
                    bucket["leader_url"] = leader_url.split("?")[0]
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, code)
    return groups


def _registry_entries(groups: dict[str, dict]) -> list[dict]:
    entries = []
    for code, meta in sorted(groups.items()):
        entries.append(
            {
                "code": code,
                "name": meta["name"],
                "home_url": meta.get("home_url") or "",
                "leader_intro_url": meta.get("leader_url") or "",
                "appointment_list_url": "" if code in NO_APPT_CODES else (meta.get("appt_url") or ""),
                "level": "city",
                "parent_code": "jilin",
                "region": "吉林省",
                "notes": ["jilin_manual"] + (["NO_APPT"] if code in NO_APPT_CODES else []),
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

    detail_dir = dest / "leader_details"
    detail_dir.mkdir(exist_ok=True)
    archived_details: list[Path] = []
    for i, src in enumerate(meta.get("leader_detail_files") or []):
        target = detail_dir / f"detail_{i}.html"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        archived_details.append(target)
        manifest["files"].append(f"leader_details/detail_{i}.html")

    appt_archived = None
    if meta.get("appt_list_html"):
        appt_archived = dest / "appt_list.html"
        src = meta["appt_list_html"]
        if src.resolve() != appt_archived.resolve():
            shutil.copy2(src, appt_archived)
        manifest["files"].append("appt_list.html")

    meta["archived_leader_details"] = archived_details
    meta["archived_appt_list"] = appt_archived
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_detail_files") or [])
        if meta.get("appt_list_html"):
            paths.add(meta["appt_list_html"])
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


def _needs_detail_enrich(duty: LeaderDuty) -> bool:
    if duty.departments_raw:
        return False
    summary = (duty.duty_summary or "").strip()
    if summary in ("主持全面工作", "负责全面工作"):
        return False
    return True


def _to_http(url: str) -> str:
    if url.startswith("https://"):
        return "http://" + url[len("https://") :]
    return url


def _parse_leaders(
    code: str,
    detail_paths: list[Path],
    *,
    try_fetch: bool,
) -> tuple[LeaderCrawlResult, list[str], dict]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    detail_fetch_stats = {"attempted": 0, "ok": 0, "failed": 0, "with_departments": 0, "skipped_waf": 0}
    details_dir = ARCHIVE / code / "leader_details"
    details_dir.mkdir(parents=True, exist_ok=True)

    detail_urls: list[str] = []
    seen_urls: set[str] = set()
    for path in detail_paths:
        html = _read_html(path)
        detail_url = _saved_url(html, f"file:///{path.as_posix()}")
        hub = detail_url or hub
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))
        for url in _jilin_leader_detail_urls(html):
            key = _to_http(url.split("?", 1)[0].rstrip("/"))
            if key in seen_urls:
                continue
            seen_urls.add(key)
            detail_urls.append(key)

    for cache_path in sorted(details_dir.glob("fetch_*.html")):
        html = _read_html(cache_path)
        # Skip tiny WAF challenge caches from prior failed runs.
        if len(html) < 2000 and ("$_ts" in html or "nsd=" in html):
            continue
        detail_url = _saved_url(html, f"file:///{cache_path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))

    by_name = {d.person_name: d for d in all_duties if is_plausible_person_name(d.person_name)}

    if try_fetch and detail_urls:
        session = create_session()
        consecutive_fail = 0
        for url in detail_urls:
            if consecutive_fail >= 2:
                detail_fetch_stats["skipped_waf"] += 1
                continue
            detail_fetch_stats["attempted"] += 1
            slug = re.sub(r"[^\w.-]+", "_", url.rsplit("/", 1)[-1])[:80]
            if slug.endswith(".html"):
                slug = slug[: -len(".html")]
            cache_path = details_dir / f"fetch_{slug}.html"
            try:
                if cache_path.exists() and cache_path.stat().st_size > 2000:
                    detail_html = _read_html(cache_path)
                    if len(detail_html) < 2000 or "$_ts" in detail_html[:500]:
                        cache_path.unlink(missing_ok=True)
                        raise RuntimeError("cached WAF shell")
                    detail_url = url
                else:
                    time.sleep(0.35)
                    detail_url, detail_html = fetch_html(
                        session,
                        _to_http(url),
                        referer=_to_http(hub or BASE),
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    if len(detail_html) < 2000 or "$_ts" in detail_html[:800]:
                        raise RuntimeError("WAF challenge page")
                    cache_path.write_text(detail_html, encoding="utf-8")
                duties = parse_leader_intro(detail_html, detail_url, code)
                if not duties:
                    detail_fetch_stats["failed"] += 1
                    consecutive_fail += 1
                    continue
                detail_fetch_stats["ok"] += 1
                consecutive_fail = 0
                for duty in duties:
                    if duty.departments_raw:
                        detail_fetch_stats["with_departments"] += 1
                    prev = by_name.get(duty.person_name)
                    if prev is None or _duty_richness(duty) > _duty_richness(prev):
                        by_name[duty.person_name] = duty
            except Exception as exc:  # noqa: BLE001
                detail_fetch_stats["failed"] += 1
                consecutive_fail += 1
                logging.debug("leader detail fetch skip %s: %s", url, exc)
        session.close()
        all_duties = list(by_name.values())

    missing = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and _needs_detail_enrich(d)
    ]
    return (
        LeaderCrawlResult(
            bureau=code,
            hub_url=hub,
            page_count=len(detail_paths),
            leaders=all_duties,
        ),
        missing,
        detail_fetch_stats,
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

    path = meta.get("archived_appt_list")
    if path is None or not path.exists():
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    html = _read_html(path)
    list_url = _saved_url(html, meta.get("appt_url") or f"file:///{path.as_posix()}")
    items = parse_appointment_list(html, list_url)
    notices = []
    events = []
    failed = []
    session = create_session() if try_fetch else None
    notices_dir = ARCHIVE / code / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    consecutive_fail = 0
    try:
        for item in items:
            if item.source_url in known:
                continue
            if not try_fetch or session is None:
                continue
            if consecutive_fail >= 2:
                failed.append({"url": item.source_url, "error": "skipped_after_waf"})
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1].split("?")[0])[:80]
            cache_path = notices_dir / f"fetch_{slug}.html"
            try:
                time.sleep(0.25)
                if cache_path.exists() and cache_path.stat().st_size > 2000:
                    detail_html = _read_html(cache_path)
                    if len(detail_html) < 2000 or "$_ts" in detail_html[:800]:
                        cache_path.unlink(missing_ok=True)
                        raise RuntimeError("cached WAF shell")
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(
                        session,
                        _to_http(item.source_url),
                        referer=_to_http(list_url),
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    if len(detail_html) < 2000 or "$_ts" in detail_html[:800]:
                        raise RuntimeError("WAF challenge page")
                    cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
                consecutive_fail = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_fail += 1
                failed.append({"url": item.source_url, "error": str(exc)})
    finally:
        if session:
            session.close()

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    logging.info("classified %s Jilin cities", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report: dict = {
        "cities": [],
        "stats": {},
        "leader_missing_departments": {},
        "leader_detail_fetch": {},
        "appt_failed": {},
        "no_appt": sorted(NO_APPT_CODES),
    }

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_detail_files": len(meta.get("leader_detail_files") or []),
                "has_appt": code not in NO_APPT_CODES and bool(meta.get("appt_list_html")),
                "no_appt": code in NO_APPT_CODES,
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    reload_sites()
    resume_state = load_resume_state()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    for code, meta in sorted(groups.items()):
        if should_skip_city(resume_state, JOB_ID, code, resume=args.resume):
            continue
        details = meta.get("archived_leader_details") or []
        lead_n = appt_list_n = appt_events_n = 0
        lr = None
        ar = None
        if details:
            lr, missing, fetch_stats = _parse_leaders(code, details, try_fetch=args.try_fetch)
            lead_n = len(lr.leaders)
            if missing:
                report["leader_missing_departments"][code] = missing
            report["leader_detail_fetch"][code] = fetch_stats
            logging.info(
                "LEAD %s %s leaders (%s missing 分管) detail_fetch=%s",
                code,
                lead_n,
                len(missing),
                fetch_stats,
            )
        if code not in NO_APPT_CODES:
            ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            appt_list_n = ar.list_count
            appt_events_n = len(ar.events)
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
        else:
            logging.info("APPT %s NO_APPT (四平/辽源)", code)

        city_stats = {
            "name": meta["name"],
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
            job_id=JOB_ID,
            resume_state=resume_state,
            stats=city_stats,
        )

    dump_json("output/jilin_manual_report.json", report)

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
