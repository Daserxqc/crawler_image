# -*- coding: utf-8 -*-
"""Ingest 计划单列市 manual HTML from output/manual/ root into parent provinces.

Handles loose files for 青岛/深圳/厦门/大连 (宁波已在浙江 manual 入库).
Archives under output/manual/{parent}/{code}/ and moves sources to
output/manual/{parent}/_source/.
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

MANUAL_ROOT = ROOT / "output" / "manual"

# code -> parent province folder / registry parent_code
PLAN_CITIES = {
    "shandong_qingdao": {
        "parent": "shandong",
        "region": "山东省",
        "name": "国家税务总局青岛市税务局",
        "home_url": "https://qingdao.chinatax.gov.cn/",
        "leader_intro_url": "https://qingdao.chinatax.gov.cn/xxgk2019/ldjj/",
        "appointment_list_url": "https://qingdao.chinatax.gov.cn/xxgk2019/rsxx/rsrm/",
        "filename_keys": ("青岛",),
        "domain_keys": ("qingdao.chinatax.gov.cn",),
    },
    "guangdong_shenzhen": {
        "parent": "guangdong",
        "region": "广东省",
        "name": "国家税务总局深圳市税务局",
        "home_url": "https://shenzhen.chinatax.gov.cn/",
        "leader_intro_url": "https://shenzhen.chinatax.gov.cn/sztax/zgj/leader_tt.shtml",
        "appointment_list_url": (
            "https://shenzhen.chinatax.gov.cn/sztax/zdgkml/zsjs/rsjy/gkmlrsrm/zfxxgk_zdgk_list.shtml"
        ),
        "filename_keys": ("深圳",),
        "domain_keys": ("shenzhen.chinatax.gov.cn",),
    },
    "fujian_fj_xmsswj": {
        "parent": "fujian",
        "region": "福建省",
        "name": "国家税务总局厦门市税务局",
        "home_url": "https://xiamen.chinatax.gov.cn/",
        "leader_intro_url": (
            "http://xiamen.chinatax.gov.cn/xmswcms/zfxxgk/fdzdgknr/jggk/ldjj/page/index.html"
        ),
        "appointment_list_url": (
            "http://xiamen.chinatax.gov.cn/xmswcms/zfxxgk/fdzdgknr/zsjs/rsjy/rsrm/page/index.html"
        ),
        "filename_keys": ("厦门",),
        "domain_keys": ("xiamen.chinatax.gov.cn",),
    },
    "liaoning_dalian": {
        "parent": "liaoning",
        "region": "辽宁省",
        "name": "国家税务总局大连市税务局",
        "home_url": "https://dalian.chinatax.gov.cn/",
        "leader_intro_url": "https://dalian.chinatax.gov.cn/col/col2888/index.html?number=0103",
        "appointment_list_url": "https://dalian.chinatax.gov.cn/col/col3734/index.html",
        "filename_keys": ("大连",),
        "domain_keys": ("dalian.chinatax.gov.cn",),
    },
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


def _saved_url(html: str, fallback: str) -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'<meta name="url" content="([^"]+)"', html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _code_from_html(html: str, filename: str) -> str:
    saved = _saved_url(html, "")
    blob = f"{filename}\n{saved}\n{html[:8000]}"
    for code, meta in PLAN_CITIES.items():
        if any(k in filename for k in meta["filename_keys"]):
            return code
        if any(d in blob.lower() for d in meta["domain_keys"]):
            return code
    return ""


def _is_appt_file(html: str, filename: str) -> bool:
    if "人事任免" in filename or ("任免" in filename and "领导" not in filename):
        return True
    if "领导简介" in filename or "领导专栏" in filename:
        return False
    saved = _saved_url(html, "")
    if any(x in saved for x in ("/rsrm", "/rsxx/rsrm", "gkmlrsrm", "col3734")):
        return True
    if "人事任免" in html[:3000] and "领导简介" not in filename:
        return "任免" in filename or "zfxxgk" in saved
    return False


def _duty_richness(duty: LeaderDuty) -> tuple[int, int, int]:
    deps = duty.departments_raw or []
    return (
        len(deps),
        1 if (duty.duty_summary or "").strip() else 0,
        len(duty.title_raw or ""),
    )


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

    def _bucket(code: str) -> dict:
        city = PLAN_CITIES[code]
        return groups.setdefault(
            code,
            {
                "code": code,
                "name": city["name"],
                "parent": city["parent"],
                "leader_files": [],
                "appt_list_html": None,
                "appt_iframe_html": None,
            },
        )

    # 1) Loose files at output/manual/*.html
    for path in sorted(MANUAL_ROOT.glob("*.html")):
        html = _read_html(path)
        code = _code_from_html(html, path.name)
        if not code:
            logging.debug("skip non-plan-city %s", path.name)
            continue
        bucket = _bucket(code)
        if _is_appt_file(html, path.name):
            bucket["appt_list_html"] = path
            iframe = MANUAL_ROOT / f"{path.stem}_files" / "search.html"
            if iframe.is_file():
                bucket["appt_iframe_html"] = iframe
        else:
            bucket["leader_files"].append(path)

    # 2) Already-archived under output/manual/{parent}/{code}/
    for code, city in PLAN_CITIES.items():
        dest = MANUAL_ROOT / city["parent"] / code
        if not dest.is_dir():
            continue
        bucket = _bucket(code)
        for leader in sorted((dest / "leaders").glob("leader_*.html")) if (dest / "leaders").is_dir() else []:
            if leader not in bucket["leader_files"]:
                bucket["leader_files"].append(leader)
        appt = dest / "appt_list.html"
        if appt.is_file() and not bucket.get("appt_list_html"):
            bucket["appt_list_html"] = appt
        iframe = dest / "appt_iframe_search.html"
        if iframe.is_file() and not bucket.get("appt_iframe_html"):
            bucket["appt_iframe_html"] = iframe
        # Also check parent _source for iframe beside moved appt portal
        source_dir = MANUAL_ROOT / city["parent"] / "_source"
        if source_dir.is_dir() and not bucket.get("appt_iframe_html"):
            for search in source_dir.glob("*_files/search.html"):
                # match Qingdao portal assets
                if "青岛" in search.parent.name or "qingdao" in search.parent.name.lower():
                    bucket["appt_iframe_html"] = search

    return {k: v for k, v in groups.items() if v.get("leader_files") or v.get("appt_list_html") or v.get("appt_iframe_html")}


def _archive_group(code: str, meta: dict) -> Path:
    parent = meta["parent"]
    dest = MANUAL_ROOT / parent / code
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"code": code, "name": meta["name"], "parent": parent, "files": []}

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

    iframe_archived = None
    if meta.get("appt_iframe_html") and Path(meta["appt_iframe_html"]).is_file():
        iframe_archived = dest / "appt_iframe_search.html"
        src = Path(meta["appt_iframe_html"])
        if src.resolve() != iframe_archived.resolve():
            shutil.copy2(src, iframe_archived)
        manifest["files"].append("appt_iframe_search.html")

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived
    meta["archived_appt_iframe"] = iframe_archived
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    """Move loose root HTML + *_files into output/manual/{parent}/_source/."""
    for code, meta in groups.items():
        parent = meta["parent"]
        source_dir = MANUAL_ROOT / parent / "_source"
        source_dir.mkdir(parents=True, exist_ok=True)
        paths: set[Path] = set(meta.get("leader_files") or [])
        if meta.get("appt_list_html"):
            paths.add(meta["appt_list_html"])
        for path in sorted(paths):
            if not path.exists() or path.parent != MANUAL_ROOT:
                continue
            dest = source_dir / path.name
            if dest.resolve() != path.resolve():
                if dest.exists():
                    dest.unlink()
                shutil.move(str(path), str(dest))
            asset_dir = MANUAL_ROOT / f"{path.stem}_files"
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

    if try_fetch and paths:
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


def _appt_list_items(code: str, meta: dict) -> tuple[str, list]:
    """Prefer Qingdao iframe search.html; else main appt_list.html."""
    city = PLAN_CITIES[code]
    default_url = city["appointment_list_url"] or ""

    iframe = meta.get("archived_appt_iframe")
    if iframe and Path(iframe).exists():
        html = _read_html(Path(iframe))
        list_url = default_url or _saved_url(html, f"file:///{Path(iframe).as_posix()}")
        items = parse_appointment_list(html, list_url)
        if items:
            return list_url, items

    path = meta.get("archived_appt_list")
    if path is None or not Path(path).exists():
        return default_url, []
    html = _read_html(Path(path))
    list_url = _saved_url(html, default_url or f"file:///{Path(path).as_posix()}")
    items = parse_appointment_list(html, list_url)
    return list_url, items


def _rewrite_detail_url(code: str, url: str) -> str:
    """Xiamen list links point at SPA shells; real articles are /content/Sxxxx.html."""
    if code != "fujian_fj_xmsswj":
        return url
    m = re.search(r"[?&]id=(S\d+)\b", url, re.I)
    if m:
        return f"http://xiamen.chinatax.gov.cn/content/{m.group(1)}.html"
    m = re.search(r"/zdgkml\.html\?id=(S\d+)", url, re.I)
    if m:
        return f"http://xiamen.chinatax.gov.cn/content/{m.group(1)}.html"
    return url


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    list_url, items = _appt_list_items(code, meta)
    notices = []
    events = []
    failed = []
    session = create_session() if try_fetch else None
    try:
        for item in items:
            detail_url = _rewrite_detail_url(code, item.source_url)
            # Skip only if BOTH original and rewritten URLs already known.
            if item.source_url in known and detail_url in known:
                continue
            if detail_url in known and detail_url != item.source_url:
                continue
            if not try_fetch or session is None:
                continue
            try:
                time.sleep(0.25)
                final_url, detail_html = fetch_html(
                    session, detail_url, referer=list_url, follow_meta_refresh=False
                )
                notice = parse_appointment_detail(detail_html, final_url, code)
                notices.append(notice)
                events.extend(extract_appointment_events(notice))
            except Exception as exc:  # noqa: BLE001
                failed.append({"url": detail_url, "error": str(exc)})
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


def _patch_registry() -> int:
    registry = load_city_registry()
    by_code = {e["code"]: e for e in registry if e.get("code")}
    updated = 0
    for code, city in PLAN_CITIES.items():
        merged = {
            **by_code.get(code, {}),
            "code": code,
            "name": city["name"],
            "home_url": city["home_url"],
            "leader_intro_url": city["leader_intro_url"],
            "appointment_list_url": city["appointment_list_url"],
            "level": "city",
            "parent_code": city["parent"],
            "region": city["region"],
            "manual_skip": True,
            "discovery_source": "plan_city_manual",
        }
        notes = list(merged.get("notes") or [])
        if "plan_city" not in notes:
            notes.append("plan_city")
        merged["notes"] = notes
        if by_code.get(code) != merged:
            updated += 1
        by_code[code] = merged
    save_city_registry(list(by_code.values()))
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="output/tax_hr.db")
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated codes to process (default: all classified)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    patched = _patch_registry()
    logging.info("patched %s plan-city registry entries", patched)

    groups = _classify_files()
    only = {c.strip() for c in args.only.split(",") if c.strip()}
    if only:
        groups = {k: v for k, v in groups.items() if k in only}
    logging.info("classified %s plan cities: %s", len(groups), ", ".join(sorted(groups)))

    report: dict = {"cities": [], "leader_missing_bio": {}, "ingest": {}}

    for code, meta in sorted(groups.items()):
        dest = _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "parent": meta["parent"],
                "archive": str(dest.relative_to(ROOT)),
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": bool(meta.get("appt_list_html") or meta.get("appt_iframe_html")),
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
        if leaders:
            lr, missing = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
            lead_results.append(lr)
            if missing:
                report["leader_missing_bio"][code] = missing
            logging.info(
                "LEAD %s %s leaders (%s missing bio)",
                code,
                len(lr.leaders),
                len(missing),
            )
        ar = _parse_appointments(code, meta, known=known, try_fetch=args.try_fetch)
        if ar.list_count or ar.notices:
            appt_results.append(ar)
        logging.info(
            "APPT %s list=%s notices=%s events=%s failed=%s",
            code,
            ar.list_count,
            len(ar.notices),
            len(ar.events),
            len(ar.failed),
        )

    by_lead = {r.bureau: r for r in lead_results}
    by_appt = {r.bureau: r for r in appt_results}
    report["cities"] = [
        {
            **c,
            "leaders": len(by_lead[c["code"]].leaders) if c["code"] in by_lead else 0,
            "appt_list": by_appt[c["code"]].list_count if c["code"] in by_appt else 0,
            "appt_notices": len(by_appt[c["code"]].notices) if c["code"] in by_appt else 0,
            "appt_events": len(by_appt[c["code"]].events) if c["code"] in by_appt else 0,
            "appt_failed": len(by_appt[c["code"]].failed) if c["code"] in by_appt else 0,
        }
        for c in report["cities"]
    ]

    appt_payload = appointments_payload(appt_results) if appt_results else []
    lead_payload = leaders_payload(lead_results) if lead_results else []
    if appt_payload:
        dump_json("output/plan_cities_manual_appointments.json", appt_payload)
    if lead_payload:
        dump_json("output/plan_cities_manual_leaders.json", lead_payload)

    conn = connect(args.db)
    try:
        if appt_payload:
            report["ingest"]["appt"] = ingest_appointment_results(appt_payload, conn=conn)
            logging.info("ingest appt %s", report["ingest"]["appt"])
        if lead_payload:
            report["ingest"]["lead"] = ingest_leader_results(lead_payload, conn=conn)
            logging.info("ingest lead %s", report["ingest"]["lead"])
        n = recompute_persons(conn)
        conn.commit()
        logging.info("recomputed %s persons", n)
        report["ingest"]["persons_recomputed"] = n
    finally:
        conn.close()

    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML into parent province _source/ folders")

    report_path = MANUAL_ROOT / "plan_cities_ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for parent in sorted({m["parent"] for m in groups.values()}):
        parent_report = MANUAL_ROOT / parent / "plan_city_ingest_report.json"
        parent_report.parent.mkdir(parents=True, exist_ok=True)
        subset = {
            **report,
            "cities": [c for c in report["cities"] if c["parent"] == parent],
        }
        parent_report.write_text(json.dumps(subset, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
