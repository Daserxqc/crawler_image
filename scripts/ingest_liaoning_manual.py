# -*- coding: utf-8 -*-
"""Archive + ingest Liaoning city manual HTML from output/manual/liaoning/."""

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
from tax_platform.crawler.leader_intro import leader_detail_urls, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult, leaders_payload
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import ingest_appointment_results, ingest_leader_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "liaoning"
ARCHIVE = MANUAL
BASE = "http://liaoning.chinatax.gov.cn"
COL_CODE_RE = re.compile(r"/col/col(\d+)/", re.I)
HIDDEN_RE = re.compile(r'id="(?P<key>[^"]+)"[^>]*>(?P<val>[^<]+)')
ART_URL_RE = re.compile(r"/art/\d{4}/\d{1,2}/\d{1,2}/art_\d+_\d+\.html", re.I)
NO_APPT_CODES: frozenset[str] = frozenset()

CITY_SUFFIXES = (
    "沈抚改革创新示范区",
    "沈阳市",
    "鞍山市",
    "抚顺市",
    "本溪市",
    "丹东市",
    "锦州市",
    "营口市",
    "阜新市",
    "辽阳市",
    "铁岭市",
    "朝阳市",
    "盘锦市",
    "葫芦岛市",
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


def _abs_col_url(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith("http"):
        return path
    return f"{BASE}{path if path.startswith('/') else '/' + path}"


def _bureau_name_from_city(city_label: str) -> str:
    city = (city_label or "").strip()
    if not city:
        return ""
    if city.endswith("示范区"):
        return f"国家税务总局辽宁{city}税务局"
    if not city.endswith("市"):
        city = f"{city}市"
    return f"国家税务总局{city}税务局"


def _code_from_html(html: str, filename: str) -> tuple[str, str]:
    loc = _hidden_map(html)
    city_m = COL_CODE_RE.search(loc.get("col4_url", ""))
    if city_m:
        col_id = city_m.group(1)
        code = f"liaoning_col{col_id}"
        city_name = loc.get("col4_name") or ""
        return code, _bureau_name_from_city(city_name)

    # Leader list pages often omit hiddenLocation — parse breadcrumb city hub link.
    soup_html = html
    for m in re.finditer(
        r'href="(?:https?://liaoning\.chinatax\.gov\.cn)?(/col/col(\d+)/index\.html)"[^>]*>([^<]+)</a>',
        soup_html,
        re.I,
    ):
        label = re.sub(r"\s+", "", m.group(3))
        if not label or label in {"各市页面", "信息公开", "领导简介", "法定主动公开内容"}:
            continue
        if label.endswith("市") or "示范区" in label:
            col_id = m.group(2)
            return f"liaoning_col{col_id}", _bureau_name_from_city(label)

    column_type = _hidden(html, "ColumnType") or ""
    if column_type.endswith("市局"):
        city_name = column_type.replace("市局", "市")
        bureau = _bureau_name_from_city(city_name)
        for suffix in CITY_SUFFIXES:
            if suffix.startswith(city_name.replace("市", "")) or city_name in suffix:
                # Without col id, still allow bureau match via registry later.
                return "", bureau
        return "", bureau
    for suffix in CITY_SUFFIXES:
        if suffix in filename:
            return "", _bureau_name_from_city(suffix)
    return "", loc.get("col4_name", "")


def _is_appt_file(html: str, filename: str) -> bool:
    col1 = _hidden(html, "col1_name")
    if col1 in ("人事信息", "人事任免"):
        return True
    if "法定主动公开" in filename and "领导" not in filename:
        return True
    if ("人事信息" in filename or "人事任免" in filename) and "领导" not in filename:
        return True
    return False


def _is_leader_list_file(html: str, filename: str) -> bool:
    if "领导简介" in filename or "领导专栏" in filename:
        if ART_URL_RE.search(_saved_url(html, "")):
            return True
        return _hidden(html, "i_articleid") in ("0", "") or _hidden(html, "pagetype") == "2"
    return _hidden(html, "col1_name") in ("领导简介", "领导专栏") and _hidden(html, "i_articleid") in (
        "0",
        "",
    )


def _is_leader_detail_file(html: str, filename: str) -> bool:
    saved = _saved_url(html, "")
    if not ART_URL_RE.search(saved):
        return False
    if _is_leader_list_file(html, filename):
        return False
    if _hidden(html, "i_articleid") not in ("0", ""):
        return True
    duties = parse_leader_intro(html, saved or f"file:///{filename}", "liaoning_probe")
    if len(duties) == 1 and (duties[0].departments_raw or duties[0].duty_summary):
        return True
    if "领导简介" not in filename and ART_URL_RE.search(saved):
        return len(duties) == 1
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


def _classify_files() -> dict[str, dict]:
    groups: dict[str, dict] = {}
    sources = [MANUAL]
    source_archive = MANUAL / "_source"
    if source_archive.is_dir():
        sources.append(source_archive)

    registry = {
        e["code"]: e for e in load_city_registry() if str(e.get("code", "")).startswith("liaoning_col")
    }

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            code, bureau_name = _code_from_html(html, path.name)
            if not code:
                # Match existing registry entry by bureau name when col id only in breadcrumb meta.
                if bureau_name:
                    for reg in registry.values():
                        if reg.get("name") == bureau_name:
                            code = reg["code"]
                            break
            if not code:
                if _is_leader_detail_file(html, path.name):
                    saved = _saved_url(html, "")
                    logging.warning("skip unclassified detail (save with city list page): %s (%s)", path.name, saved)
                else:
                    logging.warning("skip unclassified %s", path.name)
                continue
            reg = registry.get(code, {})
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "name": reg.get("name") or bureau_name,
                    "leader_files": [],
                    "leader_detail_files": [],
                    "appt_list_html": None,
                    "leader_url": reg.get("leader_intro_url", ""),
                    "appt_url": reg.get("appointment_list_url", ""),
                    "home_url": reg.get("home_url", ""),
                },
            )
            if bureau_name and not bucket["name"]:
                bucket["name"] = bureau_name
            loc = _hidden_map(html)
            col1_url = _abs_col_url(loc.get("col1_url", ""))
            col4_url = _abs_col_url(loc.get("col4_url", ""))
            if col4_url:
                bucket["home_url"] = col4_url
            if _is_appt_file(html, path.name):
                bucket["appt_list_html"] = path
                if col1_url:
                    bucket["appt_url"] = col1_url
            elif _is_leader_list_file(html, path.name):
                bucket["leader_files"].append(path)
                if col1_url:
                    bucket["leader_url"] = col1_url
            elif _is_leader_detail_file(html, path.name):
                bucket["leader_detail_files"].append(path)
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, code)
    return groups


def _registry_entries(groups: dict[str, dict]) -> list[dict]:
    entries = []
    for code, meta in sorted(groups.items()):
        home = meta.get("home_url") or ""
        leader = meta.get("leader_url") or ""
        appt = meta.get("appt_url") or ""
        entries.append(
            {
                "code": code,
                "name": meta["name"],
                "home_url": home,
                "leader_intro_url": leader,
                "appointment_list_url": appt,
                "level": "city",
                "parent_code": "liaoning",
                "region": "辽宁省",
                "notes": ["liaoning_manual"],
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

    meta["archived_leaders"] = archived_leaders
    meta["archived_leader_details"] = archived_details
    meta["archived_appt_list"] = appt_archived
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_files") or [])
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
    if duty.duty_summary == "主持全面工作":
        return False
    if duty.departments_raw:
        return False
    return True


def _parse_leaders(
    code: str,
    list_paths: list[Path],
    detail_paths: list[Path],
    *,
    try_fetch: bool,
) -> tuple[LeaderCrawlResult, list[str], dict]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    detail_fetch_stats = {"attempted": 0, "ok": 0, "failed": 0, "with_departments": 0}

    for path in list_paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))

    for path in detail_paths:
        html = _read_html(path)
        detail_url = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))

    details_dir = ARCHIVE / code / "leader_details"
    details_dir.mkdir(parents=True, exist_ok=True)

    for cache_path in sorted(details_dir.glob("fetch_*.html")):
        html = _read_html(cache_path)
        detail_url = _saved_url(html, f"file:///{cache_path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))
    by_name = {d.person_name: d for d in all_duties if is_plausible_person_name(d.person_name)}

    detail_urls: list[str] = []
    seen_urls: set[str] = set()
    for path in list_paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        for url in leader_detail_urls(html, hub):
            key = url.split("?", 1)[0].rstrip("/")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            detail_urls.append(url)

    if try_fetch and detail_urls and list_paths:
        session = create_session()
        for url in detail_urls:
            detail_fetch_stats["attempted"] += 1
            slug = re.sub(r"[^\w.-]+", "_", url.rsplit("/", 1)[-1].split("?")[0])[:80]
            if slug.endswith(".html"):
                slug = slug[: -len(".html")]
            cache_path = details_dir / f"fetch_{slug}.html"
            try:
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = url
                else:
                    time.sleep(0.35)
                    detail_url, detail_html = fetch_html(
                        session,
                        url,
                        referer=hub,
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    cache_path.write_text(detail_html, encoding="utf-8")
                duties = parse_leader_intro(detail_html, detail_url, code)
                if not duties:
                    detail_fetch_stats["failed"] += 1
                    continue
                detail_fetch_stats["ok"] += 1
                for duty in duties:
                    if duty.departments_raw:
                        detail_fetch_stats["with_departments"] += 1
                    prev = by_name.get(duty.person_name)
                    if prev is None or _duty_richness(duty) > _duty_richness(prev):
                        by_name[duty.person_name] = duty
            except Exception as exc:  # noqa: BLE001
                detail_fetch_stats["failed"] += 1
                logging.debug("leader detail fetch skip %s: %s", url, exc)
        session.close()
        all_duties = list(by_name.values())

    missing_departments = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and _needs_detail_enrich(d)
    ]
    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(list_paths) + len(detail_paths), leaders=all_duties),
        missing_departments,
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
    list_url = _saved_url(html, f"file:///{path.as_posix()}")
    items = parse_appointment_list(html, list_url)
    notices = []
    events = []
    failed = []
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
                time.sleep(0.25)
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    logging.info("classified %s Liaoning cities", len(groups))
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
                "leader_files": len(meta.get("leader_files") or []),
                "leader_detail_files": len(meta.get("leader_detail_files") or []),
                "has_appt": code not in NO_APPT_CODES and bool(meta.get("appt_list_html")),
            }
        )

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    reload_sites()
    conn = connect(args.db)
    known = known_notice_urls(conn=conn)
    conn.close()

    appt_results = []
    lead_results = []
    for code, meta in sorted(groups.items()):
        leaders = meta.get("archived_leaders") or []
        details = meta.get("archived_leader_details") or []
        lead_n = appt_list_n = appt_events_n = 0
        if leaders or details:
            lr, missing, fetch_stats = _parse_leaders(
                code,
                leaders,
                details,
                try_fetch=args.try_fetch,
            )
            lead_results.append(lr)
            lead_n = len(lr.leaders)
            if missing:
                report["leader_missing_departments"][code] = missing
            report["leader_detail_fetch"][code] = fetch_stats
            logging.info(
                "LEAD %s %s leaders (%s missing 分管/部门) detail_fetch=%s",
                code,
                lead_n,
                len(missing),
                fetch_stats,
            )
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
        report["stats"][code] = {
            "name": meta["name"],
            "leaders": lead_n,
            "appt_list": appt_list_n,
            "appt_events": appt_events_n,
        }

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/liaoning_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/liaoning_manual_leaders.json", lead_payload)

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

    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
