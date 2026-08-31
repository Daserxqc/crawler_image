# -*- coding: utf-8 -*-
"""Archive + ingest Henan city manual HTML; patch rsgl/rsrm appointment URLs.

Zhengzhou list path (user sample):
  /{slug}/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/
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
from tax_platform.store.ingest import ingest_appointment_results, known_notice_urls
from tax_platform.store.schema import connect
from tax_platform.store.tenure import recompute_persons

MANUAL = ROOT / "output" / "manual" / "henan"
BASE = "https://henan.chinatax.gov.cn"
APPT_PATH = "/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/"

# filename / page markers → registry code
CITY_MARKERS = (
    ("郑州实验区", "henan_path_zhengzhoushiyanqu"),
    ("郑州市", "henan_path_zhengzhou"),
    ("开封市", "henan_path_kaifeng"),
    ("洛阳市", "henan_path_luoyang"),
    ("平顶山市", "henan_path_pingdingshan"),
    ("安阳市", "henan_path_anyang"),
    ("鹤壁市", "henan_path_hebi"),
    ("新乡市", "henan_path_xinxiang"),
    ("焦作市", "henan_path_jiaozuo"),
    ("濮阳市", "henan_path_puyang"),
    ("许昌市", "henan_path_xuchang"),
    ("漯河市", "henan_path_luohe"),
    ("三门峡市", "henan_path_sanmenxia"),
    ("南阳市", "henan_path_nanyang"),
    ("商丘市", "henan_path_shangqiu"),
    ("信阳市", "henan_path_xinyang"),
    ("周口市", "henan_path_zhoukou"),
    ("驻马店市", "henan_path_zhumadian"),
    ("济源产城融合示范区", "henan_path_jiyuan"),
    ("济源市", "henan_path_jiyuan"),
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


def _slug_from_code(code: str) -> str:
    # henan_path_zhengzhou → zhengzhou
    return code.removeprefix("henan_path_")


def _appt_url(slug: str) -> str:
    return f"{BASE}/{slug}{APPT_PATH}"


def _guess_code(name: str, html: str) -> str | None:
    blob = name + "\n" + html[:8000]
    for marker, code in CITY_MARKERS:
        if marker in blob:
            return code
    m = re.search(r"henan\.chinatax\.gov\.cn/([a-z]+)/xxgk", html, re.I)
    if m:
        slug = m.group(1).lower()
        code = f"henan_path_{slug}"
        return code
    return None


def _canonical_list_url(html: str, code: str) -> str:
    m = re.search(
        r'https?://henan\.chinatax\.gov\.cn/[a-z]+/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/?',
        html,
        re.I,
    )
    if m:
        return m.group(0).rstrip("/") + "/"
    # Saved-as often has relative /zhengzhou/...
    m = re.search(r'/([a-z]+)/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/?', html, re.I)
    if m:
        return _appt_url(m.group(1).lower())
    return _appt_url(_slug_from_code(code))


def _patch_all_henan_registry() -> int:
    rows = load_city_registry()
    n = 0
    for row in rows:
        code = row.get("code") or ""
        if not code.startswith("henan_path_"):
            continue
        slug = _slug_from_code(code)
        want = _appt_url(slug)
        if row.get("appointment_list_url") != want:
            row["appointment_list_url"] = want
            notes = row.get("notes")
            if isinstance(notes, list):
                notes.append("appt_url_rsgl_rsrm_fixed")
            n += 1
    if n:
        save_city_registry(rows)
    return n


def _classify_root_files() -> dict[str, dict]:
    groups: dict[str, dict] = {}
    for path in sorted(MANUAL.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".html", ".htm"}:
            continue
        if path.name.startswith("_"):
            continue
        html = _read_html(path)
        code = _guess_code(path.name, html)
        if not code:
            logging.warning("skip unclassified %s", path.name)
            continue
        meta = groups.setdefault(
            code,
            {"leader_files": [], "appt_files": [], "appt_url": "", "name": ""},
        )
        is_appt = "人事任免" in path.name or "任免工作人员" in path.name or "/rsrm" in html[:5000]
        if is_appt or "人事" in path.name:
            meta["appt_files"].append(path)
            meta["appt_url"] = _canonical_list_url(html, code)
        else:
            meta["leader_files"].append(path)
        for marker, c in CITY_MARKERS:
            if c == code:
                meta["name"] = f"国家税务总局{marker}税务局".replace("实验区税务局", "郑州实验区税务局")
                break
    return groups


def _archive(code: str, meta: dict) -> Path:
    dest = MANUAL / code
    dest.mkdir(parents=True, exist_ok=True)
    notices = dest / "notices"
    notices.mkdir(exist_ok=True)
    for src in meta.get("appt_files") or []:
        target = dest / "appt_list.html"
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        # move sibling _files if present
        files_dir = src.with_name(src.stem + "_files")
        if files_dir.is_dir():
            out_files = dest / "appt_list_files"
            if out_files.exists():
                shutil.rmtree(out_files, ignore_errors=True)
            shutil.copytree(files_dir, out_files)
    for i, src in enumerate(meta.get("leader_files") or []):
        target = dest / "leaders" / f"leader_{i}.html"
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(src, target)
    manifest = {
        "code": code,
        "name": meta.get("name"),
        "appt_url": meta.get("appt_url"),
        "appt_files": [p.name for p in meta.get("appt_files") or []],
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dest


def _ingest_city(
    code: str,
    *,
    try_fetch: bool,
    session,
    known: set[str],
) -> dict:
    dest = MANUAL / code
    list_path = dest / "appt_list.html"
    if not list_path.exists():
        return {"code": code, "error": "no appt_list.html"}

    html = _read_html(list_path)
    list_url = _canonical_list_url(html, code)
    items = parse_appointment_list(html, list_url)
    logging.info("LIST %s items=%s url=%s", code, len(items), list_url)

    notices_dir = dest / "notices"
    notices_dir.mkdir(exist_ok=True)
    notices: list = []
    events = []
    failed: list[dict[str, str]] = []

    for i, item in enumerate(items):
        abs_url = item.source_url
        if abs_url in known:
            logging.info("skip known %s", abs_url)
            continue
        body = ""
        local = notices_dir / f"notice_{i}.html"
        if try_fetch and session is not None:
            try:
                final_url, body = fetch_html(
                    session, abs_url, allow_browser=True, follow_meta_refresh=False
                )
                abs_url = final_url or abs_url
                local.write_text(body, encoding="utf-8")
                time.sleep(0.4)
            except Exception as exc:  # noqa: BLE001
                logging.warning("fetch fail %s: %s", abs_url, exc)
                failed.append({"url": abs_url, "error": str(exc)})
                continue
        elif local.exists():
            body = _read_html(local)
        else:
            failed.append({"url": abs_url, "error": "no local body"})
            continue

        try:
            notice = parse_appointment_detail(body, abs_url, code)
            notices.append(notice)
            events.extend(extract_appointment_events(notice))
        except Exception as exc:  # noqa: BLE001
            failed.append({"url": abs_url, "error": str(exc)})

    result = AppointmentCrawlResult(
        bureau=code,
        list_url=list_url,
        list_count=len(items),
        notices=notices,
        events=events,
        failed=failed,
        skipped=0,
    )
    conn = connect()
    try:
        n = ingest_appointment_results(appointments_payload([result]), conn=conn)
        recompute_persons(conn)
        conn.commit()
    finally:
        conn.close()
    logging.info(
        "INGEST %s list=%s notices=%s events=%s failed=%s inserted=%s",
        code,
        result.list_count,
        len(notices),
        len(events),
        len(failed),
        n,
    )
    return {
        "code": code,
        "list": result.list_count,
        "notices": len(notices),
        "events": len(events),
        "failed": len(failed),
    }


def _existing_city_dirs() -> dict[str, dict]:
    """Cities already archived under ``henan_path_*/appt_list.html``."""
    groups: dict[str, dict] = {}
    for dest in sorted(MANUAL.glob("henan_path_*")):
        if not dest.is_dir():
            continue
        list_path = dest / "appt_list.html"
        if not list_path.exists():
            continue
        code = dest.name
        groups[code] = {
            "leader_files": [],
            "appt_files": [list_path],
            "appt_url": _appt_url(_slug_from_code(code)),
            "name": "",
        }
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--try-fetch", action="store_true")
    parser.add_argument("--archive-source", action="store_true")
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--patch-only", action="store_true")
    parser.add_argument(
        "--from-archived",
        action="store_true",
        help="Re-ingest henan_path_*/appt_list.html even if root HTML already archived",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    patched = _patch_all_henan_registry()
    logging.info("patched %s Henan appointment_list_url → rsgl/rsrm", patched)
    reload_sites()
    if args.patch_only:
        return 0

    groups = _classify_root_files()
    if args.from_archived or not groups:
        archived = _existing_city_dirs()
        for code, meta in archived.items():
            groups.setdefault(code, meta)
    if args.codes:
        groups = {k: v for k, v in groups.items() if k in args.codes}
    # Anyang: site has no appointment column (user confirmed).
    groups.pop("henan_path_anyang", None)
    logging.info("classified %s Henan cities", len(groups))

    for code, meta in list(groups.items()):
        # Only re-copy when sources still sit in MANUAL root.
        if any(p.parent == MANUAL for p in meta.get("appt_files") or []):
            _archive(code, meta)
            logging.info("archived %s appt_files=%s", code, len(meta.get("appt_files") or []))

    if args.archive_source:
        src_dir = MANUAL / "_source"
        src_dir.mkdir(exist_ok=True)
        for meta in groups.values():
            for src in meta.get("appt_files") or []:
                if src.parent == MANUAL and src.exists():
                    shutil.move(str(src), str(src_dir / src.name))
                files_dir = MANUAL / (src.stem + "_files")
                if files_dir.is_dir() and files_dir.parent == MANUAL:
                    dest = src_dir / files_dir.name
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True)
                    shutil.move(str(files_dir), str(dest))

    session = create_session() if args.try_fetch else None
    known: set[str] = set()
    conn = connect()
    try:
        known = known_notice_urls(conn=conn)
    finally:
        conn.close()

    report = {"cities": [], "stats": {}}
    for code in groups:
        stats = _ingest_city(code, try_fetch=args.try_fetch, session=session, known=known)
        report["cities"].append(stats)
        report["stats"][code] = stats
    if session:
        session.close()
    dump_json("output/manual/henan/ingest_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
