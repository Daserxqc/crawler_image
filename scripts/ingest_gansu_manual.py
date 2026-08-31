# -*- coding: utf-8 -*-
"""Archive + ingest Gansu city manual HTML from output/manual/gansu/."""

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
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "gansu"
ARCHIVE = MANUAL
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)
HIDDEN_RE = re.compile(r'id="(?P<key>[^"]+)"[^>]*>(?P<val>[^<]+)')
NO_APPT_CODES: frozenset[str] = frozenset()

HUB_TO_NAME = {
    "160": "国家税务总局兰州市税务局",
    "161": "国家税务总局嘉峪关市税务局",
    "271": "国家税务总局酒泉市税务局",
    "272": "国家税务总局张掖市税务局",
    "273": "国家税务总局金昌市税务局",
    "274": "国家税务总局武威市税务局",
    "275": "国家税务总局白银市税务局",
    "276": "国家税务总局定西市税务局",
    "277": "国家税务总局天水市税务局",
    "278": "国家税务总局陇南市税务局",
    "279": "国家税务总局平凉市税务局",
    "280": "国家税务总局庆阳市税务局",
    "281": "国家税务总局临夏回族自治州税务局",
    "282": "国家税务总局甘南藏族自治州税务局",
    "283": "国家税务总局兰州新区税务局",
}

REGISTRY_ENTRIES = [
    {"code": "gansu_col160", "name": "国家税务总局兰州市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col160/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col3886/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col3788/index.html"},
    {"code": "gansu_col161", "name": "国家税务总局嘉峪关市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col161/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col3975/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col3998/index.html"},
    {"code": "gansu_col271", "name": "国家税务总局酒泉市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col271/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col4291/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col4314/index.html"},
    {"code": "gansu_col272", "name": "国家税务总局张掖市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col272/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col4917/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col4940/index.html"},
    {"code": "gansu_col273", "name": "国家税务总局金昌市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col273/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col5286/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col5309/index.html"},
    {"code": "gansu_col274", "name": "国家税务总局武威市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col274/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col5377/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col5400/index.html"},
    {"code": "gansu_col275", "name": "国家税务总局白银市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col275/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col4412/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col4430/index.html"},
    {"code": "gansu_col276", "name": "国家税务总局定西市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col276/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col5571/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col5594/index.html"},
    {"code": "gansu_col277", "name": "国家税务总局天水市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col277/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col5901/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col5924/index.html"},
    {"code": "gansu_col278", "name": "国家税务总局陇南市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col278/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col6240/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col6263/index.html"},
    {"code": "gansu_col279", "name": "国家税务总局平凉市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col279/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col6606/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col6629/index.html"},
    {"code": "gansu_col280", "name": "国家税务总局庆阳市税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col280/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col6936/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col6959/index.html"},
    {"code": "gansu_col281", "name": "国家税务总局临夏回族自治州税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col281/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col7274/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col7297/index.html"},
    {"code": "gansu_col282", "name": "国家税务总局甘南藏族自治州税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col282/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col7648/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col7671/index.html"},
    {"code": "gansu_col283", "name": "国家税务总局兰州新区税务局", "home_url": "http://gansu.chinatax.gov.cn/col/col283/index.html", "leader_intro_url": "http://gansu.chinatax.gov.cn/col/col4017/index.html", "appointment_list_url": "http://gansu.chinatax.gov.cn/col/col4040/index.html"},
]


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
    for key in ("ColumnName", "channel"):
        m = re.search(rf'<meta name="{key}" content="([^"]+)"', html, re.I)
        if m:
            out[key] = m.group(1).strip()
    return out


def _hidden(html: str, key: str) -> str:
    return _hidden_map(html).get(key, "")


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r'<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)', html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta name="server" content="([^"]+)"', html, re.I)
    if m and m.group(1).strip().startswith("http"):
        return m.group(1).strip()
    return fallback


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    loc = _hidden_map(html)
    hub_m = COL_CODE_RE.search(loc.get("col5_url", ""))
    if hub_m:
        hub = hub_m.group(1)
        code = f"gansu_col{hub}"
        name = HUB_TO_NAME.get(hub) or loc.get("col5_name") or code
        if name and not name.startswith("国家税务总局"):
            name = f"国家税务总局{name}"
        return code, name
    for entry in REGISTRY_ENTRIES:
        if entry["name"][:8] in filename:
            return entry["code"], entry["name"]
    return "", loc.get("col5_name", "")


def _is_appt_file(html: str, filename: str) -> bool:
    col1 = _hidden(html, "col1_name")
    if col1 in ("人事信息", "人事任免"):
        return True
    if ("人事信息" in filename or "人事任免" in filename) and "领导专栏" not in filename:
        return True
    channel = _hidden(html, "ColumnName") or _hidden(html, "channel")
    return channel in ("人事信息", "人事任免")


def _is_leader_file(html: str, filename: str) -> bool:
    if "领导专栏" in filename:
        return True
    return _hidden(html, "col1_name") == "领导专栏"


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
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)
    registry = {e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("gansu_")}
    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            code, bureau_name = _code_from_html(html, path.name)
            if not code:
                logging.warning("skip unclassified %s", path.name)
                continue
            reg = registry.get(code, {})
            bucket = groups.setdefault(code, {"code": code, "name": reg.get("name") or bureau_name, "leader_files": [], "appt_list_html": None})
            if _is_appt_file(html, path.name):
                bucket["appt_list_html"] = path
            elif _is_leader_file(html, path.name):
                bucket["leader_files"].append(path)
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, code)
    return groups


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
    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
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


def _patch_registry() -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for entry in REGISTRY_ENTRIES:
        code = entry["code"]
        merged = {**by_code.get(code, {}), **entry, "level": "city", "parent_code": "gansu", "region": "甘肃省"}
        notes = list(merged.get("notes") or [])
        if "gansu_manual" not in notes:
            notes.append("gansu_manual")
        merged["notes"] = notes
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    reload_sites()
    return updated


def _bio_complete(duty: LeaderDuty) -> bool:
    return bool((duty.title_raw or "").strip()) and bool((duty.duty_summary or "").strip() or (duty.departments_raw or []))


def _parse_leaders(code: str, paths: list[Path], *, try_fetch: bool) -> tuple[LeaderCrawlResult, list[str], dict]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    fetch_stats = {"targets": 0, "fetched": 0, "errors": []}
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))
    missing_bio = [d.person_name for d in all_duties if is_plausible_person_name(d.person_name) and not _bio_complete(d)]
    if try_fetch and paths:
        session = create_session()
        by_name = {d.person_name: d for d in all_duties}
        seen_urls: set[str] = set()
        for path in paths:
            html = _read_html(path)
            hub = _saved_url(html, f"file:///{path.as_posix()}")
            targets = leader_page_targets(html, hub)
            fetch_stats["targets"] += len(targets)
            for url in targets:
                key = url.split("?", 1)[0].rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                if not re.search(r"/art/\d+/", url):
                    continue
                try:
                    time.sleep(0.25)
                    final, detail = fetch_html(session, url, referer=hub, follow_meta_refresh=False)
                    fetch_stats["fetched"] += 1
                    for duty in parse_leader_intro(detail, final, code):
                        if not is_plausible_person_name(duty.person_name):
                            continue
                        prev = by_name.get(duty.person_name)
                        if prev is None or _duty_richness(duty) > _duty_richness(prev):
                            by_name[duty.person_name] = duty
                except Exception as exc:  # noqa: BLE001
                    fetch_stats["errors"].append({"url": url, "error": str(exc)})
        session.close()
        all_duties = list(by_name.values())
        missing_bio = [d.person_name for d in all_duties if is_plausible_person_name(d.person_name) and not _bio_complete(d)]
    return LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(paths), leaders=all_duties), missing_bio, fetch_stats


def _parse_appointments(code: str, meta: dict, *, known: set[str], try_fetch: bool) -> AppointmentCrawlResult:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)
    path = meta.get("archived_appt_list")
    if path is None or not path.exists():
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)
    html = _read_html(path)
    list_url = _saved_url(html, f"file:///{path.as_posix()}")
    items = parse_appointment_list(html, list_url)
    notices, events, failed = [], [], []
    session = create_session() if try_fetch else None
    notices_dir = ARCHIVE / code / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    try:
        for item in items:
            if item.source_url in known or not try_fetch or session is None:
                continue
            slug = re.sub(r"[^\w.-]+", "_", item.source_url.rsplit("/", 1)[-1].split("?")[0])[:80]
            cache_path = notices_dir / f"fetch_{slug}.html"
            try:
                time.sleep(0.2)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(session, item.source_url, referer=list_url, follow_meta_refresh=False)
                    cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": item.source_url, "error": str(exc)})
    finally:
        if session:
            session.close()
    return AppointmentCrawlResult(bureau=code, list_url=list_url, list_count=len(items), notices=notices, events=events, failed=failed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    patched = _patch_registry()
    logging.info("patched %s registry entries", patched)
    groups = _classify_files()
    logging.info("classified %s Gansu cities", len(groups))
    report = {"cities": [], "leader_missing_bio": {}, "leader_fetch": {}, "appt_failed": {}, "no_appt": sorted(NO_APPT_CODES)}
    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append({"code": code, "name": meta["name"], "leader_files": len(meta.get("leader_files") or []), "has_appt": code not in NO_APPT_CODES and bool(meta.get("appt_list_html"))})
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()
    appt_results, lead_results = [], []
    for code, meta in sorted(groups.items()):
        leaders = meta.get("archived_leaders") or []
        if leaders:
            lr, missing, fetch_stats = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
            lead_results.append(lr)
            report["leader_fetch"][code] = fetch_stats
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info("LEAD %s %s leaders (%s missing bio, fetch %s/%s)", code, len(lr.leaders), len(missing), fetch_stats.get("fetched", 0), fetch_stats.get("targets", 0))
        if code not in NO_APPT_CODES:
            ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
            if ar.list_count or ar.notices:
                appt_results.append(ar)
            if ar.failed:
                report["appt_failed"][code] = ar.failed[:10]
            logging.info("APPT %s list=%s notices=%s events=%s failed=%s", code, ar.list_count, len(ar.notices), len(ar.events), len(ar.failed))
    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/gansu_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/gansu_manual_leaders.json", lead_payload)
    conn = connect(args.db)
    try:
        if appt_payload:
            logging.info("ingest appt %s", ingest_appointment_results(appt_payload, conn=conn))
        if lead_payload:
            logging.info("ingest lead %s", ingest_leader_results(lead_payload, conn=conn))
            from tax_platform.normalize.person import is_plausible_person_name

            junk = frozenset({"局长", "副局长", "总会计师", "总经济师", "总审计师", "隐私声明"})
            removed = 0
            for code in groups:
                rows = conn.execute(
                    "SELECT id, person_name FROM leader_duties WHERE bureau_code = ?",
                    (code,),
                ).fetchall()
                for row_id, person_name in rows:
                    if is_plausible_person_name(person_name) and person_name not in junk:
                        continue
                    conn.execute("DELETE FROM leader_duties WHERE id = ?", (row_id,))
                    removed += 1
            if removed:
                logging.info("pruned %s sidebar junk leader rows", removed)
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
    finally:
        conn.close()
    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
