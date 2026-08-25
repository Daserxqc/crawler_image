"""Ingest half-done / zero-batch provinces from user-saved HTML under output/.

Leaders: parse saved pages; HTTP-follow sibling leader links (headless if SSL/WAF).
Appointments: parse list from saved HTML; headless-only for notice bodies.
Seeds PROVINCE_URL_OVERRIDES from saved-from URLs. Does not commit.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites_provinces import PROVINCE_URL_OVERRIDES
from tax_platform.crawler.appointment_clauses import extract_appointment_events
from tax_platform.crawler.appointment_detail import parse_appointment_detail
from tax_platform.crawler.appointment_job import AppointmentCrawlResult
from tax_platform.crawler.appointment_list import parse_appointment_list
from tax_platform.crawler.http_client import create_session, fetch_html
from tax_platform.crawler.job_io import dump_json, serialize_crawl_result
from tax_platform.crawler.leader_intro import leader_page_targets, parse_leader_intro
from tax_platform.crawler.leader_job import LeaderCrawlResult
from tax_platform.models.entities import LeaderDuty
from tax_platform.store import ingest_appointment_results, ingest_leader_results

OUT = ROOT / "output"
APPT_LIMIT = 15

# side: "leaders" | "appointments" | "both"
# needles match substrings in output/*.html filenames
PROVINCES: dict[str, dict[str, Any]] = {
    # --- half-done: missing side ---
    "beijing": {
        "side": "appointments",
        "appt_needles": ("人事任免",),
        "appt_fallback": "http://beijing.chinatax.gov.cn/bjswj/c105858/cs_li.shtml",
        "appt_prefer": "人事任免.html",
    },
    "tianjin": {
        "side": "both",
        "leader_needles": ("天津",),
        "leader_fallback": "https://tianjin.chinatax.gov.cn/u_zlmView.action?fjdm=11200000000&lmdm=010002",
        "leader_follow": True,
        "leader_iframe": "01000201.html",
        # Saved HTML is leader shell only; appointment list must be live-fetched.
        "appt_needles": ("天津", "任免"),
        "appt_fallback": "https://tianjin.chinatax.gov.cn/u_zlmViewMx.action?fjdm=11200000000&lmdm=01000401",
        "appt_live_list": True,
    },
    "shanxi": {
        "side": "appointments",
        "appt_needles": ("山西",),
        "appt_fallback": "https://shanxi.chinatax.gov.cn/web/list/sx-11400-4187",
    },
    "zhejiang": {
        "side": "leaders",
        "leader_needles": ("浙江", "领导"),
        "leader_fallback": "https://zhejiang.chinatax.gov.cn/col/col10697/index.html",
        "leader_follow": True,
    },
    "fujian": {
        "side": "appointments",
        "appt_needles": ("福建", "人事"),
        "appt_fallback": "https://fujian.chinatax.gov.cn/zfxxgkzl/zfxxgkml/zsjs/ryzl_838/",
    },
    "henan": {
        "side": "appointments",
        "appt_needles": ("河南", "任免"),
        "appt_fallback": "https://henan.chinatax.gov.cn/xxgk/rsgl/rsrm/",
    },
    "hubei": {
        "side": "both",
        "leader_needles": ("湖北",),
        "leader_fallback": "http://hubei.chinatax.gov.cn/hbsw/xxgk/ldjj/index.html",
        "leader_follow": False,
        # Saved HTML is 领导简介 only; no 人事任免 list on disk.
        "appt_needles": ("湖北", "任免"),
        "appt_fallback": "http://hubei.chinatax.gov.cn/hbsw/xxgk/rsrm/index.html",
        "appt_live_list": True,
    },
    "hunan": {
        "side": "appointments",
        "appt_needles": ("湖南", "任免"),
        "appt_fallback": "https://hunan.chinatax.gov.cn/lists/20190715095001",
    },
    "qinghai": {
        "side": "leaders",
        "leader_needles": ("青海",),
        "leader_fallback": "http://qinghai.chinatax.gov.cn/web/sjtwz/ldjs.shtml",
        "leader_prefer": "谭中伟_国家税务总局青海省税务局.html",
        "leader_follow": True,
    },
    "ningxia": {
        "side": "leaders",
        "leader_needles": ("宁夏", "领导"),
        "leader_fallback": "https://ningxia.chinatax.gov.cn/col/col14807/index.html",
        "leader_follow": False,
    },
    # --- zero-batch (hebei already done) ---
    "jilin": {
        "side": "both",
        "leader_needles": ("吉林", "领导"),
        "leader_fallback": "https://jilin.chinatax.gov.cn/col/col24472/index.html",
        "appt_needles": ("吉林", "人事"),
        "appt_fallback": "https://jilin.chinatax.gov.cn/col/col8211/index.html",
        "skip_if_done": True,
    },
    "heilongjiang": {
        "side": "both",
        "leader_needles": ("黑龙江", "领导"),
        "leader_fallback": "http://heilongjiang.chinatax.gov.cn/col/col11194/index.html",
        "appt_needles": ("黑龙江", "人事"),
        "appt_fallback": "http://heilongjiang.chinatax.gov.cn/col/col11190/index.html",
        "skip_if_done": True,
    },
    "jiangxi": {
        "side": "both",
        "leader_needles": ("江西", "史峰"),
        "leader_prefer": "国家税务总局江西省税务局 史峰 史峰.html",
        "leader_fallback": "https://jiangxi.chinatax.gov.cn/col/col39358/index.html",
        "leader_follow": True,
        "appt_needles": ("江西", "人事"),
        "appt_fallback": "https://jiangxi.chinatax.gov.cn/col/col32613/index.html",
    },
    "sichuan": {
        "side": "both",
        "leader_needles": ("四川", "姜涛"),
        "leader_fallback": "https://sichuan.chinatax.gov.cn/col/col20404/index.html",
        "leader_follow": True,
        "appt_needles": ("四川", "任免"),
        "appt_fallback": "https://sichuan.chinatax.gov.cn/col/col20006/index.html?number=A002001",
    },
    "yunnan": {
        "side": "both",
        "leader_needles": ("云南", "领导简介"),
        "leader_fallback": "https://yunnan.chinatax.gov.cn/col/col4701/index.html",
        "appt_needles": ("云南", "任免"),
        "appt_fallback": "https://yunnan.chinatax.gov.cn/col/col8641/index.html",
    },
    "xizang": {
        "side": "both",
        "leader_needles": ("西藏", "任伟"),
        "leader_fallback": "https://xizang.chinatax.gov.cn/col/col15212/index.html",
        "leader_follow": True,
        "appt_needles": ("西藏", "任免"),
        "appt_fallback": "https://xizang.chinatax.gov.cn/col/col15000/index.html",
    },
    "neimenggu": {
        "side": "both",
        "leader_needles": ("内蒙古", "领导"),
        "leader_prefer": "国家税务总局内蒙古自治区税务局- 领导简介.html",
        "leader_fallback": "https://neimenggu.chinatax.gov.cn/xxgk/ldjj/",
        "leader_follow": True,
        "appt_needles": ("内蒙古", "人事任免"),
        "appt_prefer": "国家税务总局内蒙古自治区税务局- 人事任免.html",
        "appt_fallback": "https://neimenggu.chinatax.gov.cn/xxgk/rsxx_25345/",
    },
    "hainan": {
        "side": "both",
        "leader_needles": ("海南",),
        "leader_prefer": "李文涛-国家税务总局海南省税务局.html",
        "leader_fallback": "https://hainan.chinatax.gov.cn/xxgk_1_13_1/",
        "leader_follow": False,  # sidebar already has full title:name list
        "appt_needles": ("海南", "任免"),
        "appt_prefer": "人事任免-国家税务总局海南省税务局.html",
        "appt_fallback": "https://hainan.chinatax.gov.cn/xxgk_3/",
    },
    "chongqing": {
        "side": "both",
        "leader_needles": ("重庆",),
        "leader_prefer": "国家税务总局重庆市税务局.html",
        "leader_fallback": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/ldjj/",
        "leader_follow": True,
        "leader_live_hub": True,
        "appt_needles": ("重庆",),
        "appt_prefer": "国家税务总局重庆市税务局.html",
        "appt_fallback": "https://chongqing.chinatax.gov.cn/cqtax/xxgk/rsxx/rsrm/",
    },
    "shaanxi": {
        "side": "both",
        "leader_needles": ("陕西", "袁继军"),
        "leader_fallback": "https://shaanxi.chinatax.gov.cn/col/col36377/index.html",
        "appt_needles": ("陕西", "人事"),
        "appt_fallback": "https://shaanxi.chinatax.gov.cn/col/col9016/index.html",
    },
    "gansu": {
        "side": "both",
        "leader_needles": ("甘肃", "吴毓壮"),
        "leader_fallback": "http://gansu.chinatax.gov.cn/col/col3858/index.html",
        "appt_needles": ("甘肃", "任免"),
        "appt_fallback": "http://gansu.chinatax.gov.cn/col/col129/index.html?number=A00009B00001A00016",
    },
    "xinjiang": {
        "side": "both",
        "leader_needles": ("新疆", "领导简介"),
        "leader_fallback": (
            "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/jggk/ldjj/"
            "202605/t20260521_157910.html"
        ),
        "appt_needles": ("新疆", "政府信息公开"),
        "appt_fallback": "https://xinjiang.chinatax.gov.cn/zwgk/xjsw/fdzdgknr/rsjy/rsrm_22397/",
    },
    "hebei": {
        "side": "both",
        "skip_if_done": True,
        "leader_needles": ("河北", "领导简介"),
        "leader_fallback": "http://hebei.chinatax.gov.cn/hbsw/xxgk/jj/202112/t20211231_3016798.html",
        "appt_needles": ("河北",),
        "appt_fallback": "http://hebei.chinatax.gov.cn/hbswxxgk/gkml/1166/1247/1757/index.html",
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
        # Prefer the decode with the most CJK and fewest replacement chars.
        # Hubei/Tianjin shell saves are often gb18030 mislabeled as utf-8.
        score = len(re.findall(r"[\u4e00-\u9fa5]", text)) - text.count("\ufffd") * 50
        if best is None or score > best[0]:
            best = (score, text)
    if best is not None:
        return best[1]
    return raw.decode("utf-8", errors="replace")


def _saved_url(html: str, fallback: str) -> str:
    match = re.search(r"saved from url=\(\d+\)(https?://[^ )\s]+)", html, re.I)
    return match.group(1) if match else fallback


def _newest_html(*needles: str, prefer: str | None = None) -> Path:
    if prefer:
        preferred = OUT / prefer
        if preferred.is_file():
            return preferred
    cands = [p for p in OUT.glob("*.html") if all(n in p.name for n in needles)]
    if not cands:
        raise FileNotFoundError(f"No HTML in {OUT} matching {needles}")
    return max(cands, key=lambda p: p.stat().st_mtime)


def _existing_counts(code: str) -> tuple[int, int]:
    L = E = 0
    lf = OUT / "provinces" / f"{code}_leaders.json"
    af = OUT / "provinces" / f"{code}_appointments.json"
    if lf.exists():
        data = json.loads(lf.read_text(encoding="utf-8"))
        L = len(data.get("leaders") or [])
    if af.exists():
        data = json.loads(af.read_text(encoding="utf-8"))
        E = len(data.get("events") or [])
    return L, E


def _seed_override(code: str, *, leader_url: str | None = None, appt_url: str | None = None) -> None:
    slot = PROVINCE_URL_OVERRIDES.setdefault(code, {})
    if leader_url:
        slot["leader_intro_url"] = leader_url
    if appt_url:
        slot["appointment_list_url"] = appt_url


def _merge_leaders(*groups: list[LeaderDuty]) -> list[LeaderDuty]:
    by_name: dict[str, LeaderDuty] = {}
    order: list[str] = []
    for group in groups:
        for duty in group:
            prev = by_name.get(duty.person_name)
            if prev is None:
                by_name[duty.person_name] = duty
                order.append(duty.person_name)
                continue
            # Prefer the entry with a real title / richer bio.
            if (not prev.title_raw) and duty.title_raw:
                by_name[duty.person_name] = duty
            elif prev.title_raw and duty.title_raw and not prev.gender and duty.gender:
                by_name[duty.person_name] = duty
    return [by_name[n] for n in order]


def _needs_leader_follow(cfg: dict[str, Any], leaders: list[LeaderDuty]) -> bool:
    if not cfg.get("leader_follow"):
        return False
    if len(leaders) < 6:
        return True
    # Hub sidebar may list all names without titles — still follow siblings.
    return any(not (d.title_raw or "").strip() for d in leaders)


def _fetch_page(session: Any, url: str, *, referer: str | None = None, allow_browser: bool) -> tuple[str, str]:
    return fetch_html(
        session,
        url,
        referer=referer,
        follow_meta_refresh=True,
        allow_browser=allow_browser,
    )


def _looks_like_leader_detail(url: str, hub_url: str) -> bool:
    path = url.lower()
    if any(token in path for token in ("ldjs", "ldjj", "ldjianjie", "010002", "leaderlist", "/ld_")):
        return True
    hub_art = re.search(r"art_(\d+)_", hub_url, re.I)
    if hub_art and re.search(rf"art_{hub_art.group(1)}_", path, re.I):
        return True
    # Zhejiang / Ningxia style article under leader column
    if re.search(r"/art/\d{4}/\d{1,2}/\d{1,2}/art_(10697|14807|4701|36377|9220)_", path):
        return True
    # Sichuan 领导专栏 cols
    if re.search(r"/col/col204\d{2}/", path):
        return True
    # Person column pages: /art/.../art_<colId>_... or /col/col<id>/
    # Jiangxi cols span ~39358–41658; Xizang leader cols ~14702–15212.
    # Reject low site-chrome cols (5xxx) that are not near the hub.
    art_col = re.search(r"/art/\d{4}/\d{1,2}/\d{1,2}/art_(\d+)_", path, re.I)
    tgt_col = re.search(r"/col/col(\d+)", path, re.I)
    hub_col = re.search(r"/col/col(\d+)", hub_url, re.I) or re.search(
        r"/art/\d{4}/\d{1,2}/\d{1,2}/art_(\d+)_", hub_url, re.I
    )
    col_id = None
    if art_col:
        col_id = int(art_col.group(1))
    elif tgt_col:
        col_id = int(tgt_col.group(1))
    if col_id is not None:
        if hub_col:
            hid = int(hub_col.group(1))
            if abs(hid - col_id) <= 3000:
                return True
        # Standalone person-detail art under a high column id (leader cluster)
        if art_col and col_id >= 10000:
            return True
        if tgt_col and col_id >= 10000 and "/col/col" in hub_url.lower():
            return True
    return False


def _enrich_leaders_from_targets(
    code: str,
    html: str,
    hub_url: str,
    base: list[LeaderDuty],
    *,
    allow_browser: bool = True,
) -> list[LeaderDuty]:
    targets = [
        u for u in leader_page_targets(html, hub_url) if _looks_like_leader_detail(u, hub_url)
    ]
    if not targets:
        return base
    session = create_session()
    collected = list(base)
    for url in targets[:16]:
        try:
            time.sleep(0.15)
            detail_url, detail_html = _fetch_page(
                session, url, referer=hub_url, allow_browser=allow_browser
            )
            collected = _merge_leaders(collected, parse_leader_intro(detail_html, detail_url, code))
        except Exception as exc:  # noqa: BLE001
            logging.warning("%s leader follow failed %s: %s", code, url, exc)
    return collected


def ingest_leaders(code: str) -> dict[str, Any]:
    cfg = PROVINCES[code]
    hub = str(cfg.get("leader_fallback") or "")
    allow_browser = True

    if cfg.get("leader_live_hub"):
        # Saved file is wrong type (e.g. Chongqing 人事任免); fetch real hub.
        session = create_session()
        url, html = _fetch_page(session, hub, referer=hub, allow_browser=allow_browser)
        path_name = f"(live){hub}"
        leaders = parse_leader_intro(html, url, code)
        if _needs_leader_follow(cfg, leaders):
            leaders = _enrich_leaders_from_targets(
                code, html, url, leaders, allow_browser=allow_browser
            )
        if not leaders:
            # Don't wipe a prior good JSON when live hub is WAF/empty.
            prior_L, _ = _existing_counts(code)
            if prior_L:
                logging.warning(
                    "%s live leader hub returned 0; keeping existing %s leaders",
                    code,
                    prior_L,
                )
                return {
                    "source": path_name,
                    "url": hub,
                    "leaders": prior_L,
                    "ingested": 0,
                    "names": [],
                    "kept_existing": True,
                }
    else:
        path = _newest_html(*cfg["leader_needles"], prefer=cfg.get("leader_prefer"))
        html = _read_html(path)
        url = _saved_url(html, hub)
        path_name = path.name

        # Tianjin: body lives in saved iframe fragment
        iframe_name = cfg.get("leader_iframe")
        if iframe_name:
            files_dir = OUT / f"{path.stem}_files"
            iframe_path = files_dir / str(iframe_name)
            if iframe_path.is_file():
                iframe_html = _read_html(iframe_path)
                iframe_url = _saved_url(iframe_html, url)
                leaders = _merge_leaders(
                    parse_leader_intro(iframe_html, iframe_url, code),
                    parse_leader_intro(html, url, code),
                )
            else:
                leaders = parse_leader_intro(html, url, code)
        else:
            leaders = parse_leader_intro(html, url, code)

        if _needs_leader_follow(cfg, leaders):
            # Prefer configured hub URL so sibling col-distance checks work.
            follow_hub = hub if ("col/" in hub or "ldjj" in hub or "ldjs" in hub) else url
            leaders = _enrich_leaders_from_targets(
                code, html, follow_hub, leaders, allow_browser=allow_browser
            )

    # Prefer configured hub URL for storage when detail-page save was used
    if "col/" in hub or "ldjj" in hub or "ldjs" in hub or "leader" in hub or "lmdm=010002" in hub:
        store_url = hub
    else:
        store_url = url

    result = LeaderCrawlResult(bureau=code, hub_url=store_url, page_count=1, leaders=leaders)
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / f"{code}_leaders.json", payload)
    ingested = ingest_leader_results(payload)
    _seed_override(code, leader_url=store_url)
    return {
        "source": path_name,
        "url": store_url,
        "leaders": len(leaders),
        "ingested": ingested,
        "names": [d.person_name for d in leaders],
    }


def ingest_appointments(code: str, *, try_live: bool = True) -> dict[str, Any]:
    cfg = PROVINCES[code]
    list_url = str(cfg.get("appt_fallback") or "")
    source_name = ""

    if cfg.get("appt_live_list"):
        session = create_session()
        list_url, list_html = _fetch_page(
            session, list_url, referer=list_url, allow_browser=True
        )
        source_name = f"(live){list_url}"
        items = parse_appointment_list(list_html, list_url)
    else:
        if code == "hebei":
            # Reuse fragment under *_files/1167.html when present
            list_html_path = None
            for files_dir in sorted(OUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if files_dir.is_dir() and "河北" in files_dir.name:
                    cand = files_dir / "1167.html"
                    if cand.is_file():
                        list_html_path = cand
                        break
            if list_html_path is None:
                list_html_path = _newest_html(*cfg["appt_needles"], prefer=cfg.get("appt_prefer"))
        else:
            try:
                list_html_path = _newest_html(*cfg["appt_needles"], prefer=cfg.get("appt_prefer"))
            except FileNotFoundError:
                if cfg.get("appt_fallback"):
                    session = create_session()
                    list_url, list_html = _fetch_page(
                        session, list_url, referer=list_url, allow_browser=True
                    )
                    source_name = f"(live-fallback){list_url}"
                    items = parse_appointment_list(list_html, list_url)
                    list_html_path = None
                else:
                    raise
            if list_html_path is not None:
                list_html = _read_html(list_html_path)
                list_url = _saved_url(list_html, str(cfg["appt_fallback"]))
                items = parse_appointment_list(list_html, list_url)
                if not items and cfg.get("appt_fallback"):
                    list_url = str(cfg["appt_fallback"])
                    items = parse_appointment_list(list_html, list_url)
                source_name = (
                    list_html_path.name
                    if list_html_path.parent == OUT
                    else str(list_html_path.relative_to(OUT))
                )

    items = items[:APPT_LIMIT]

    notices = []
    events = []
    failed: list[dict[str, str]] = []
    session = create_session() if try_live else None

    for item in items:
        if not try_live or session is None:
            failed.append({"url": item.source_url, "error": "skipped live fetch (manual list only)"})
            continue
        try:
            time.sleep(0.2)
            detail_url, detail_html = fetch_html(
                session,
                item.source_url,
                referer=list_url,
                follow_meta_refresh=False,
                allow_browser=True,
            )
            notice = parse_appointment_detail(detail_html, detail_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": item.source_url, "error": str(exc)[:500]})
            logging.warning("Notice fetch failed %s: %s", item.source_url, exc)

    result = AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
    )
    payload = serialize_crawl_result(result)
    dump_json(OUT / "provinces" / f"{code}_appointments.json", payload)
    new_events = ingest_appointment_results(payload)
    _seed_override(code, appt_url=list_url)
    return {
        "source": source_name or list_url,
        "url": list_url,
        "list_items": len(items),
        "notices": len(notices),
        "events": len(events),
        "failed": len(failed),
        "new_events_ingested": new_events,
    }


def _probe_live(code: str) -> bool:
    cfg = PROVINCES[code]
    if cfg.get("appt_live_list"):
        return True
    try:
        list_path = _newest_html(*cfg["appt_needles"], prefer=cfg.get("appt_prefer"))
    except FileNotFoundError:
        return bool(cfg.get("appt_fallback"))
    list_html = _read_html(list_path)
    list_url = _saved_url(list_html, str(cfg.get("appt_fallback") or ""))
    items = parse_appointment_list(list_html, list_url)[:1]
    if not items:
        return False
    try:
        session = create_session()
        fetch_html(session, items[0].source_url, referer=list_url, allow_browser=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logging.warning("%s headless notice probe failed — list-only: %s", code, exc)
        return False


def run_province(code: str) -> dict[str, Any]:
    cfg = PROVINCES[code]
    L0, E0 = _existing_counts(code)
    summary: dict[str, Any] = {
        "code": code,
        "mode": "manual+headless",
        "prior_L": L0,
        "prior_E": E0,
    }

    if cfg.get("skip_if_done") and L0 >= 6 and E0 >= 20:
        summary["skipped"] = True
        summary["L"] = L0
        summary["E"] = E0
        summary["leader_file"] = "(existing)"
        summary["appt_file"] = "(existing)"
        return summary

    side = cfg["side"]
    leader_file = None
    appt_file = None

    if side in ("leaders", "both"):
        summary["leaders"] = ingest_leaders(code)
        leader_file = summary["leaders"]["source"]
    if side in ("appointments", "both"):
        try_live = _probe_live(code)
        summary["appointments"] = ingest_appointments(code, try_live=try_live)
        appt_file = summary["appointments"]["source"]

    L, E = _existing_counts(code)
    summary["L"] = L
    summary["E"] = E
    summary["leader_file"] = leader_file
    summary["appt_file"] = appt_file
    if side == "leaders":
        summary["missing_side_file"] = leader_file
    elif side == "appointments":
        summary["missing_side_file"] = appt_file
    else:
        summary["missing_side_file"] = f"L={leader_file}; E={appt_file}"
    return summary


def _persist_url_overrides(touched: list[str]) -> None:
    """Rewrite seeded URLs into sites_provinces.py and dump a JSON seed map."""
    from tax_platform.config.province_urls_io import merge_override, write_overrides

    seed = {code: dict(PROVINCE_URL_OVERRIDES.get(code, {})) for code in touched}
    dump_json(OUT / "province_url_seeds.json", seed)
    for code in touched:
        slot = PROVINCE_URL_OVERRIDES.get(code) or {}
        if slot:
            write_overrides(merge_override(code, slot))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    default_codes = [
        "beijing",
        "tianjin",
        "shanxi",
        "zhejiang",
        "fujian",
        "henan",
        "hubei",
        "hunan",
        "qinghai",
        "ningxia",
        "jilin",
        "heilongjiang",
        "jiangxi",
        "sichuan",
        "yunnan",
        "xizang",
        "shaanxi",
        "gansu",
        "xinjiang",
        "hebei",
        "neimenggu",
        "hainan",
        "chongqing",
    ]
    codes = sys.argv[1:] or default_codes
    rows = []
    for code in codes:
        if code not in PROVINCES:
            raise SystemExit(f"Unknown province {code}")
        logging.info("=== ingest %s ===", code)
        try:
            rows.append(run_province(code))
        except Exception as exc:  # noqa: BLE001
            logging.exception("FAILED %s", code)
            L, E = _existing_counts(code)
            rows.append({"code": code, "error": str(exc)[:500], "L": L, "E": E})
    _persist_url_overrides([r["code"] for r in rows if "error" not in r])
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
