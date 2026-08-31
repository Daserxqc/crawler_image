# -*- coding: utf-8 -*-
"""Archive + ingest Inner Mongolia city/league manual HTML from output/manual/neimenggu/.

Source HTML was saved under jilin by mistake and moved here; content is
neimenggu.chinatax.gov.cn (not crawled by this project).

兴安盟 has no personnel-appointment column (NO_APPT).
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
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.city_sites_io import load_city_registry, save_city_registry
from tax_platform.config.sites import reload_sites
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
from tax_platform.crawler.appointment_list import AppointmentListItem, parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.ingest_resume import ingest_and_checkpoint_city, should_skip_city
from tax_platform.crawler.job_io import dump_json
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.crawler.resume_state import load_resume_state
from tax_platform.models.entities import LeaderDuty
from tax_platform.normalize.person import is_plausible_person_name
from tax_platform.store.ingest import known_notice_urls
from tax_platform.store.schema import connect

MANUAL = ROOT / "output" / "manual" / "neimenggu"
ARCHIVE = MANUAL
BASE = "https://neimenggu.chinatax.gov.cn"
MSXX_RE = re.compile(r"(https?://neimenggu\.chinatax\.gov\.cn/nmgzzqswj/msxxgkml_\d+/[a-z0-9]+/)", re.I)
SLUG_RE = re.compile(r"/msxxgkml_\d+/([a-z0-9]+)/", re.I)
HREFTZ_APPT_RE = re.compile(
    r'<span[^>]*class="hreftz"[^>]*>\./(?P<path>[^<]+)</span>\s*人事任免',
    re.I,
)
DETAIL_RE = re.compile(r"/20\d{4}/t\d+_", re.I)
JOB_ID = "ingest_neimenggu_manual"

# 兴安盟无人事任免栏目
NO_APPT_CODES: frozenset[str] = frozenset({"neimenggu_xamswj"})

SLUG_TO_CITY: dict[str, str] = {
    "hhhtsswj": "呼和浩特市",
    "btsswj": "包头市",
    "whsswj": "乌海市",
    "cfsswj": "赤峰市",
    "tlsswj": "通辽市",
    "eedssswj": "鄂尔多斯市",
    "hlbesswj": "呼伦贝尔市",
    "byneswj": "巴彦淖尔市",
    "wlcbsswj": "乌兰察布市",
    "xamswj": "兴安盟",
    "xlglmswj": "锡林郭勒盟",
    "alsmswj": "阿拉善盟",
    "mzlsswj": "满洲里市",
    "elhtswj": "二连浩特市",
}

CITY_MARKERS = (
    "呼和浩特市",
    "包头市",
    "乌海市",
    "赤峰市",
    "通辽市",
    "鄂尔多斯市",
    "呼伦贝尔市",
    "巴彦淖尔市",
    "巴彦卓尔",
    "乌兰察布市",
    "兴安盟",
    "锡林郭勒盟",
    "阿拉善盟",
    "满洲里市",
    "二连浩特市",
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


def _saved_url(html: str, fallback: str = "") -> str:
    m = re.search(r"<!--\s*saved from url=\([^)]+\)(https?://[^\s>]+)", html, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    if m:
        return m.group(1).strip()
    return fallback


def _slug_from_url_or_name(url: str, filename: str) -> str:
    if url:
        m = SLUG_RE.search(url)
        if m:
            return m.group(1).lower()
    for slug, city in SLUG_TO_CITY.items():
        if city in filename:
            return slug
    if "巴彦卓尔" in filename:
        return "byneswj"
    return ""


def _code_from_slug(slug: str) -> str:
    return f"neimenggu_{slug}" if slug else ""


def _bureau_name(slug: str, fallback: str = "") -> str:
    city = SLUG_TO_CITY.get(slug, "")
    if city:
        return f"国家税务总局{city}税务局"
    if fallback.startswith("国家税务总局"):
        return fallback
    return fallback


def _hub_url(slug: str, saved: str = "") -> str:
    m = MSXX_RE.search(saved or "")
    if m:
        return m.group(1)
    return f"{BASE}/nmgzzqswj/msxxgkml_19393/{slug}/"


def _is_leader_file(filename: str) -> bool:
    return "领导简介" in filename or "领导专栏" in filename


def _is_appt_hub_file(filename: str) -> bool:
    if _is_leader_file(filename):
        return False
    return any(city in filename for city in CITY_MARKERS)


def _asset_dir(html_path: Path) -> Path:
    return html_path.parent / f"{html_path.stem}_files"


def _local_appt_index(hub_path: Path) -> Path | None:
    idx = _asset_dir(hub_path) / "index_3550.html"
    return idx if idx.is_file() else None


def _hreftz_appt_path(html: str) -> str:
    m = HREFTZ_APPT_RE.search(html)
    if not m:
        return ""
    path = m.group("path").strip().lstrip("./")
    if not path.endswith("/"):
        path += "/"
    return path


def _appt_list_url_from_hub(hub_url: str, html: str, local_index: Path | None) -> str:
    if local_index and local_index.is_file():
        local_html = _read_html(local_index)
        saved = _saved_url(local_html, "")
        # Prefer iframe that landed on 人事任免 (/1277/) rather than hub root.
        if saved and "/1277/" in saved:
            return saved
    rel = _hreftz_appt_path(html)
    if hub_url and rel:
        return urljoin(hub_url if hub_url.endswith("/") else hub_url + "/", rel + "index_3550.html")
    if local_index and local_index.is_file():
        return _saved_url(_read_html(local_index), "")
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


def _needs_detail_enrich(duty: LeaderDuty) -> bool:
    if duty.departments_raw:
        return False
    summary = (duty.duty_summary or "").strip()
    if summary in ("主持全面工作", "负责全面工作"):
        return False
    return True


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
            slug = _slug_from_url_or_name(saved, path.name)
            if not slug:
                logging.warning("skip unclassified %s", path.name)
                continue
            code = _code_from_slug(slug)
            bucket = groups.setdefault(
                code,
                {
                    "code": code,
                    "slug": slug,
                    "name": _bureau_name(slug),
                    "leader_files": [],
                    "appt_hub_html": None,
                    "appt_index_html": None,
                    "leader_url": "",
                    "appt_url": "",
                    "home_url": _hub_url(slug, saved),
                },
            )
            if _is_leader_file(path.name):
                bucket["leader_files"].append(path)
                if saved:
                    bucket["leader_url"] = saved
                    bucket["home_url"] = _hub_url(slug, saved)
            elif _is_appt_hub_file(path.name):
                bucket["appt_hub_html"] = path
                local_idx = _local_appt_index(path)
                bucket["appt_index_html"] = local_idx
                bucket["home_url"] = _hub_url(slug, saved) or bucket["home_url"]
                bucket["appt_url"] = _appt_list_url_from_hub(bucket["home_url"], html, local_idx)
            else:
                logging.warning("skip unknown kind %s (%s)", path.name, code)

    for code, meta in groups.items():
        if code in NO_APPT_CODES:
            meta["appt_url"] = ""
            continue
        if not meta.get("appt_url") and meta.get("home_url"):
            # Fallback canonical 人事任免 iframe path used across NM cities.
            meta["appt_url"] = urljoin(meta["home_url"], "1233/1240/1277/index_3550.html")
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
                "parent_code": "neimenggu",
                "region": "内蒙古自治区",
                "notes": ["neimenggu_manual"] + (["NO_APPT"] if code in NO_APPT_CODES else []),
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
    if code not in NO_APPT_CODES:
        appt_archived = dest / "appt_list.html"
        src_idx = meta.get("appt_index_html")
        copied = False
        if src_idx and Path(src_idx).is_file():
            idx_html = _read_html(Path(src_idx))
            if "/1277/" in _saved_url(idx_html, ""):
                if Path(src_idx).resolve() != appt_archived.resolve():
                    shutil.copy2(src_idx, appt_archived)
                copied = True
                manifest["files"].append("appt_list.html")
        if not copied and appt_archived.exists():
            # Keep previously try-fetched good list if present.
            if "/1277/" in _saved_url(_read_html(appt_archived), ""):
                manifest["files"].append("appt_list.html")
            else:
                appt_archived.unlink(missing_ok=True)
                appt_archived = None
        elif not copied:
            appt_archived = None

    meta["archived_leaders"] = archived_leaders
    meta["archived_appt_list"] = appt_archived if appt_archived and appt_archived.exists() else None
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def _archive_source_files(groups: dict[str, dict]) -> None:
    source_dir = MANUAL / "_source"
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: set[Path] = set()
    for meta in groups.values():
        paths.update(meta.get("leader_files") or [])
        if meta.get("appt_hub_html"):
            paths.add(meta["appt_hub_html"])
    for path in sorted(paths):
        if not path.exists() or path.parent != MANUAL:
            continue
        dest = source_dir / path.name
        if dest.resolve() != path.resolve():
            shutil.move(str(path), str(dest))
        asset_dir = _asset_dir(path)
        if asset_dir.is_dir():
            asset_dest = source_dir / asset_dir.name
            if asset_dest.exists():
                shutil.rmtree(asset_dest, ignore_errors=True)
            shutil.move(str(asset_dir), str(asset_dest))


def _parse_leaders(
    code: str,
    paths: list[Path],
    *,
    try_fetch: bool,
) -> tuple[LeaderCrawlResult, list[str], dict]:
    all_duties: list[LeaderDuty] = []
    hub = ""
    detail_fetch_stats = {"attempted": 0, "ok": 0, "failed": 0, "with_departments": 0}
    details_dir = ARCHIVE / code / "leader_details"
    details_dir.mkdir(parents=True, exist_ok=True)

    detail_urls: list[str] = []
    seen_urls: set[str] = set()
    for path in paths:
        html = _read_html(path)
        detail_url = _saved_url(html, f"file:///{path.as_posix()}")
        hub = detail_url or hub
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))
        for url in leader_page_targets(html, detail_url or BASE):
            if not DETAIL_RE.search(url):
                continue
            key = url.split("?", 1)[0].rstrip("/")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            detail_urls.append(key)

    for cache_path in sorted(details_dir.glob("fetch_*.html")):
        html = _read_html(cache_path)
        detail_url = _saved_url(html, f"file:///{cache_path.as_posix()}")
        all_duties = _merge_duties(all_duties, parse_leader_intro(html, detail_url, code))

    by_name = {d.person_name: d for d in all_duties if is_plausible_person_name(d.person_name)}

    if try_fetch and detail_urls:
        session = create_session()
        for url in detail_urls:
            detail_fetch_stats["attempted"] += 1
            slug = re.sub(r"[^\w.-]+", "_", url.rsplit("/", 1)[-1])[:80]
            if slug.endswith(".html"):
                slug = slug[: -len(".html")]
            cache_path = details_dir / f"fetch_{slug}.html"
            try:
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = url
                else:
                    time.sleep(0.4)
                    detail_url, detail_html = fetch_html(
                        session,
                        url,
                        referer=hub or BASE,
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

    missing = [
        d.person_name
        for d in all_duties
        if is_plausible_person_name(d.person_name) and _needs_detail_enrich(d)
    ]
    return (
        LeaderCrawlResult(
            bureau=code,
            hub_url=hub,
            page_count=len(paths),
            leaders=all_duties,
        ),
        missing,
        detail_fetch_stats,
    )


def _normalize_notice_url(url: str, hub_url: str, list_url: str = "") -> str:
    """Absolutize relative notice links onto the city hub; drop /1233/ tree junk."""
    hub = hub_url or ""
    if hub and not hub.endswith("/"):
        hub += "/"

    if not url.startswith("http"):
        url = urljoin(hub or list_url or (BASE + "/"), url)

    # Relative links sometimes resolve against the site root — pull back under hub.
    if hub and re.search(r"chinatax\.gov\.cn/20\d{4}/t\d+_", url, re.I):
        tail = url.split("chinatax.gov.cn/", 1)[-1]
        url = urljoin(hub, tail)

    url = re.sub(
        r"(msxxgkml_\d+/[a-z0-9]+/)(?:1233(?:/\d+)*/)+(20\d{4}/t\d+_)",
        r"\1\2",
        url,
        flags=re.I,
    )
    return url


def _city_hub(meta: dict) -> str:
    hub = (meta.get("home_url") or "").strip()
    if hub:
        return hub if hub.endswith("/") else hub + "/"
    slug = meta.get("slug") or ""
    if slug:
        return _hub_url(slug, "")
    return ""


def _load_appt_list_html(code: str, meta: dict, *, try_fetch: bool) -> tuple[str, str]:
    """Return (list_url, html). Prefers 人事任免 iframe (/1277/); try-fetches otherwise."""
    list_url = meta.get("appt_url") or ""
    hub_url = meta.get("home_url") or ""
    path = meta.get("archived_appt_list")
    html = ""

    def _usable(local_html: str, url: str) -> bool:
        saved = _saved_url(local_html, url)
        if "/1277/" not in (saved or url or ""):
            return False
        items = parse_appointment_list(local_html, url or saved or hub_url)
        return bool(items)

    if path and Path(path).exists():
        html = _read_html(Path(path))
        saved = _saved_url(html, list_url)
        if saved and "/1277/" in saved:
            list_url = saved
        if _usable(html, list_url or saved):
            return list_url or saved, html

    if try_fetch and list_url:
        session = create_session()
        try:
            time.sleep(0.35)
            final, fetched = fetch_html(
                session,
                list_url,
                referer=hub_url or BASE,
                follow_meta_refresh=False,
                allow_browser=True,
            )
            dest = ARCHIVE / code / "appt_list.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if "saved from url=" not in fetched[:500].lower():
                marker = final or list_url
                fetched = f"<!-- saved from url=({len(marker):04d}){marker} -->\n" + fetched
            dest.write_text(fetched, encoding="utf-8")
            meta["archived_appt_list"] = dest
            return final or list_url, fetched
        except Exception as exc:  # noqa: BLE001
            logging.warning("appt list fetch failed %s: %s", code, exc)
        finally:
            session.close()

    # Last resort: use local HTML even if iframe node was wrong.
    return list_url, html


def _parse_appointments(
    code: str,
    meta: dict,
    *,
    known: set[str],
    try_fetch: bool,
) -> AppointmentCrawlResult:
    if code in NO_APPT_CODES:
        return AppointmentCrawlResult(bureau=code, list_url="", list_count=0)

    list_url, html = _load_appt_list_html(code, meta, try_fetch=try_fetch)
    if not html:
        return AppointmentCrawlResult(bureau=code, list_url=list_url, list_count=0)

    hub_url = _city_hub(meta)
    # Parse against city hub so relative 20XXXX/t….html links land under the slug.
    items_raw = parse_appointment_list(html, hub_url or list_url)
    items = []
    seen: set[str] = set()
    for item in items_raw:
        url = _normalize_notice_url(item.source_url, hub_url, list_url)
        if not url.startswith("http") or "/msxxgkml_" not in url:
            logging.debug("skip bad notice url %s", url)
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append(
            AppointmentListItem(title=item.title, source_url=url, published_on=item.published_on)
        )

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
                time.sleep(0.3)
                if cache_path.exists():
                    detail_html = _read_html(cache_path)
                    detail_url = item.source_url
                else:
                    detail_url, detail_html = fetch_html(
                        session,
                        item.source_url,
                        referer=list_url or hub_url or BASE,
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
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    groups = _classify_files()
    logging.info("classified %s Neimenggu cities/leagues", len(groups))
    patched = _patch_registry(groups)
    logging.info("patched %s registry entries", patched)

    report: dict = {
        "cities": [],
        "stats": {},
        "leader_missing_departments": {},
        "leader_detail_fetch": {},
        "appt_failed": {},
        "no_appt": sorted(NO_APPT_CODES),
        "source_note": "manual HTML moved from jilin folder; neimenggu.chinatax.gov.cn",
    }

    for code, meta in sorted(groups.items()):
        _archive_group(code, meta)
        report["cities"].append(
            {
                "code": code,
                "name": meta["name"],
                "leader_files": len(meta.get("leader_files") or []),
                "has_appt": code not in NO_APPT_CODES and bool(meta.get("appt_hub_html") or meta.get("appt_url")),
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
        leaders = meta.get("archived_leaders") or []
        lead_n = appt_list_n = appt_events_n = 0
        lr = None
        ar = None
        if leaders:
            lr, missing, fetch_stats = _parse_leaders(code, leaders, try_fetch=args.try_fetch)
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
            logging.info("APPT %s NO_APPT (兴安盟)", code)

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

    dump_json("output/neimenggu_manual_report.json", report)
    report_path = ARCHIVE / "ingest_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.archive_source:
        _archive_source_files(groups)
        logging.info("archived source HTML to %s", MANUAL / "_source")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
