# -*- coding: utf-8 -*-
"""Archive + ingest Hainan city/county manual HTML from output/manual/hainan/.

市县频道 hubs: ``https://hainan.chinatax.gov.cn/sxpd_{N}/``.
Leader pages embed full roster (sidebar + inline bios); appointment lists are
static ``nrlb1-r-t-x`` links under ``sxpd_{N}_…/``.

Naming (user rule): filenames without 「xx市」 default to 市; 「xx县」 keep 县.
Prefer breadcrumb place labels from HTML when present.

保亭 has leader HTML + one saved appointment detail (no list page in folder);
not NO_APPT — detail is ingested and ``--try-fetch`` may pull the list.
"""

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
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
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

MANUAL = ROOT / "output" / "manual" / "hainan"
ARCHIVE = MANUAL
BASE = "https://hainan.chinatax.gov.cn"
SXPD_ID_RE = re.compile(r"hainan\.chinatax\.gov\.cn/sxpd_(\d+)(?:_|/|\?|#|$)", re.I)
BREADCRUMB_RE = re.compile(
    r'href="https?://hainan\.chinatax\.gov\.cn/sxpd_(\d+)"[^>]*>\s*<span>([^<]+)</span>',
    re.I,
)
DETAIL_URL_RE = re.compile(r"/sxpd_\d+(?:_\d+)*/\d+\.html", re.I)
JOB_ID = "ingest_hainan_manual"

# No unit is leader-only without appointment evidence.
NO_APPT_CODES: frozenset[str] = frozenset()

# Fallback place labels when HTML breadcrumb missing (user naming rule applied).
SXPD_PLACE: dict[str, str] = {
    "1": "海口市",
    "2": "三亚市",
    "4": "东方市",
    "5": "儋州市",
    "6": "琼海市",
    "7": "万宁市",
    "8": "文昌市",
    "9": "五指山市",
    "10": "定安县",
    "11": "白沙县",
    "12": "琼中县",
    "13": "临高县",
    "14": "澄迈县",
    "15": "保亭县",
    "16": "陵水县",
    "17": "乐东县",
    "18": "昌江县",
    "19": "屯昌县",
    "20": "三沙市",
}

COUNTY_MARKERS = ("县", "自治县")


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


def _saved_url(html: str, fallback: str = "") -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _sxpd_id_from_url(url: str) -> str:
    if not url:
        return ""
    m = SXPD_ID_RE.search(url)
    return m.group(1) if m else ""


def _apply_naming_rule(place: str) -> tuple[str, str]:
    """Return (display_place, level) using user rule: bare names → 市; 县 kept."""
    place = (place or "").strip()
    place = re.sub(r"^海南省", "", place)
    place = re.sub(r"税务局$", "", place)
    if not place:
        return "", "city"
    if any(place.endswith(m) or m in place for m in COUNTY_MARKERS if m != "县") or place.endswith("县"):
        return place, "county"
    if place.endswith(("市", "区")):
        return place, "city"
    return place + "市", "city"


def _place_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    for pat in (
        r"海南省(.+?)税务局",
        r"国家税务总局(.+?)税务局",
    ):
        m = re.search(pat, stem)
        if m:
            raw = m.group(1).strip()
            display, _ = _apply_naming_rule(raw)
            return display
    return ""


def _place_from_html(html: str, sxpd_id: str) -> str:
    for m in BREADCRUMB_RE.finditer(html):
        if m.group(1) == sxpd_id:
            label = m.group(2).strip()
            if label and label not in {"市县频道", "信息公开", "首页"}:
                display, _ = _apply_naming_rule(label)
                return display
    # Titles inside list / bio often use full bureau name.
    m = re.search(r"国家税务总局([\u4e00-\u9fa5]{2,20}(?:市|县|自治县))税务局", html)
    if m:
        display, _ = _apply_naming_rule(m.group(1))
        return display
    return SXPD_PLACE.get(sxpd_id, "")


def _bureau_name(place: str) -> str:
    if not place:
        return ""
    if place.startswith("国家税务总局"):
        return place if place.endswith("税务局") else place + "税务局"
    return f"国家税务总局{place}税务局"


def _code_from_sxpd(sxpd_id: str) -> str:
    return f"hainan_sxpd_{sxpd_id}" if sxpd_id else ""


def _is_leader_file(filename: str, html: str) -> bool:
    if "领导简介" in filename or "领导专栏" in filename:
        return True
    col = re.search(r'name="CoumnName"\s+content="([^"]+)"', html, re.I)
    return bool(col and col.group(1) in ("领导简介", "领导专栏"))


def _is_appt_list_file(filename: str, html: str, saved: str) -> bool:
    if "领导" in filename:
        return False
    if DETAIL_URL_RE.search(saved or ""):
        return False
    if "人事任免" in filename:
        return True
    col = re.search(r'name="CoumnName"\s+content="([^"]+)"', html, re.I)
    if col and col.group(1) in ("人事任免", "人事信息", "法定主动公开内容"):
        return True
    return False


def _is_appt_detail_file(filename: str, saved: str) -> bool:
    if DETAIL_URL_RE.search(saved or ""):
        return True
    if "任免" in filename and "人事任免-" not in filename and "领导" not in filename:
        return True
    return False


def _home_url(sxpd_id: str) -> str:
    return f"{BASE}/sxpd_{sxpd_id}/"


def _list_url_from_detail(saved: str) -> str:
    m = re.search(r"(https?://hainan\.chinatax\.gov\.cn/sxpd_\d+(?:_\d+)*)/\d+\.html", saved, re.I)
    if m:
        return m.group(1) + "/"
    return ""


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

    for base in sources:
        for path in sorted(base.glob("*.html")):
            if path.parent not in sources:
                continue
            html = _read_html(path)
            saved = _saved_url(html, "")
            sxpd_id = _sxpd_id_from_url(saved)
            if not sxpd_id:
                # filename fallback via known places
                place = _place_from_filename(path.name)
                for sid, label in SXPD_PLACE.items():
                    if label.rstrip("市县") in place or place.rstrip("市县") in label:
                        sxpd_id = sid
                        break
            if not sxpd_id:
                logging.warning("skip unclassified %s", path.name)
                continue

            code = _code_from_sxpd(sxpd_id)
            place = _place_from_html(html, sxpd_id) or _place_from_filename(path.name) or SXPD_PLACE.get(
                sxpd_id, ""
            )
            _, unit_level = _apply_naming_rule(place)
            bureau = _bureau_name(place)
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "sxpd_id": sxpd_id,
                    "name": bureau,
                    "place": place,
                    "unit_level": unit_level,
                    "leader_files": [],
                    "appt_list_html": None,
                    "appt_detail_files": [],
                    "leader_url": "",
                    "appt_url": "",
                    "home_url": _home_url(sxpd_id),
                },
            )
            if bureau and (not bucket["name"] or len(bureau) >= len(bucket["name"])):
                bucket["name"] = bureau
                bucket["place"] = place
                bucket["unit_level"] = unit_level

            if _is_leader_file(path.name, html):
                bucket["leader_files"].append(path)
                if saved:
                    bucket["leader_url"] = saved.split("?")[0]
                    if not bucket["leader_url"].endswith("/"):
                        bucket["leader_url"] += "/"
            elif _is_appt_list_file(path.name, html, saved):
                bucket["appt_list_html"] = path
                if saved:
                    bucket["appt_url"] = saved.split("?")[0]
                    if not bucket["appt_url"].endswith("/") and ".html" not in bucket["appt_url"]:
                        bucket["appt_url"] += "/"
            elif _is_appt_detail_file(path.name, saved):
                bucket["appt_detail_files"].append(path)
                list_url = _list_url_from_detail(saved)
                if list_url and not bucket["appt_url"]:
                    bucket["appt_url"] = list_url
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, code)

    return groups


def _registry_entries(groups: dict[str, dict]) -> list[dict]:
    entries = []
    for code, meta in sorted(groups.items(), key=lambda kv: int(kv[1].get("sxpd_id") or 0)):
        notes = ["hainan_manual", "sxpd"]
        if meta.get("unit_level") == "county":
            notes.append("county")
        if code in NO_APPT_CODES:
            notes.append("NO_APPT")
        elif not meta.get("appt_list_html") and meta.get("appt_detail_files"):
            notes.append("appt_detail_only")
        entries.append(
            {
                "code": code,
                "name": meta["name"],
                "home_url": meta.get("home_url") or "",
                "leader_intro_url": meta.get("leader_url") or "",
                "appointment_list_url": "" if code in NO_APPT_CODES else (meta.get("appt_url") or ""),
                "level": "city",
                "parent_code": "hainan",
                "region": "海南省",
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


def _parse_leaders(code: str, paths: list[Path]) -> tuple[LeaderCrawlResult, list[str]]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    for path in paths:
        html = _read_html(path)
        hub = _saved_url(html, f"file:///{path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, hub, code))

    missing = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name)
        and not (d.departments_raw or (d.duty_summary or "").strip())
    ]
    return (
        LeaderCrawlResult(bureau=code, hub_url=hub, page_count=len(paths), leaders=all_duties),
        missing,
    )


def _ensure_appt_list(code: str, meta: dict, *, try_fetch: bool) -> tuple[str, str]:
    """Return (list_url, html). May try-fetch when only detail notices exist."""
    path = meta.get("archived_appt_list")
    list_url = meta.get("appt_url") or ""
    if path and Path(path).exists():
        html = _read_html(Path(path))
        saved = _saved_url(html, list_url)
        return saved or list_url, html

    if not try_fetch or not list_url:
        return list_url, ""

    session = create_session()
    try:
        time.sleep(0.35)
        final, fetched = fetch_html(
            session,
            list_url,
            referer=meta.get("home_url") or BASE,
            follow_meta_refresh=False,
            allow_browser=True,
        )
        dest = ARCHIVE / code / "appt_list.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if "saved from url=" not in fetched[:500].lower():
            marker = final or list_url
            fetched = f"<!-- saved from url=(0020){marker} -->\n" + fetched
        dest.write_text(fetched, encoding="utf-8")
        meta["archived_appt_list"] = dest
        items = parse_appointment_list(fetched, final or list_url)
        logging.info("fetched appt list %s items=%s url=%s", code, len(items), final or list_url)
        return final or list_url, fetched
    except Exception as exc:  # noqa: BLE001
        logging.warning("appt list fetch failed %s: %s", code, exc)
        return list_url, ""
    finally:
        session.close()


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    list_url, html = _ensure_appt_list(code, meta, try_fetch=try_fetch)
    items = parse_appointment_list(html, list_url) if html else []

    notices = []
    events = []
    failed = []
    notices_dir = ARCHIVE / code / "notices"
    notices_dir.mkdir(parents=True, exist_ok=True)
    seen_notice_urls: set[str] = set()

    # Manual detail pages first.
    for path in meta.get("archived_appt_details") or []:
        detail_html = _read_html(path)
        detail_url = _saved_url(detail_html, f"file:///{path.as_posix()}")
        if detail_url in known or detail_url in seen_notice_urls:
            continue
        seen_notice_urls.add(detail_url)
        try:
            notice = parse_appointment_detail(detail_html, detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": detail_url, "error": str(exc)})

    session = create_session() if try_fetch else None
    try:
        for item in items:
            url = item.source_url
            if url in known or url in seen_notice_urls:
                continue
            if not try_fetch or session is None:
                continue
            seen_notice_urls.add(url)
            slug = re.sub(r"[^\w.-]+", "_", url.rsplit("/", 1)[-1].split("?")[0])[:80]
            cache_path = notices_dir / f"fetch_{slug}"
            if not cache_path.suffix:
                cache_path = Path(str(cache_path) + ".html")
            try:
                time.sleep(0.25)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = url
                else:
                    detail_url, detail_html = fetch_html(
                        session,
                        url,
                        referer=list_url or meta.get("home_url") or BASE,
                        follow_meta_refresh=False,
                        allow_browser=True,
                    )
                    cache_path.write_text(detail_html, encoding="utf-8")
                notice = parse_appointment_detail(detail_html, detail_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": url, "error": str(exc)})
    finally:
        if session:
            session.close()

    return AppointmentCrawlResult(
        bureau=code,
        list_url=list_url or "",
        list_count=len(items) if items else len(meta.get("archived_appt_details") or []),
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
    logging.info("classified %s Hainan units", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report: dict = {
        "cities": [],
        "stats": {},
        "leader_missing_departments": {},
        "appt_failed": {},
        "no_appt": sorted(NO_APPT_CODES),
        "naming_rule": "bare→市; 县 kept; prefer breadcrumb",
    }

    for code, meta in sorted(groups.items(), key=lambda kv: int(kv[1].get("sxpd_id") or 0)):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "place": meta.get("place"),
                "unit_level": meta.get("unit_level"),
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt_list": bool(meta.get("appt_list_html")),
                "appt_detail_files": len(meta.get("appt_detail_files") or []),
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

    for code, meta in sorted(groups.items(), key=lambda kv: int(kv[1].get("sxpd_id") or 0)):
        if should_skip_city(resume_state, JOB_ID, code, resume=args.resume):
            continue
        lead_n = appt_list_n = appt_events_n = 0
        lr = None
        ar = None
        leaders = meta.get("archived_leaders") or []
        if leaders:
            lr, missing = _parse_leaders(code, leaders)
            lead_n = len(lr.leaders)
            if missing:
                report["leader_missing_departments"][code] = missing
            logging.info("LEAD %s %s leaders (%s thin)", code, lead_n, len(missing))

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
            logging.info("APPT %s NO_APPT", code)

        city_stats = {
            "name": meta["name"],
            "place": meta.get("place"),
            "unit_level": meta.get("unit_level"),
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

    dump_json("output/hainan_manual_report.json", report)
    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
