from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tax_platform.config.sites_provinces import PROVINCE_SUBDOMAINS

PROVINCE_CODES = [code for _sub, _region, code in PROVINCE_SUBDOMAINS if code != "shanghai"]
PROGRESS = Path("output/province_batch_progress.json")
SUMMARY = Path("output/province_batch_summary.jsonl")
LOG = Path("output/province_batch.log")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run crawl_province.py for many provinces sequentially.")
    parser.add_argument("--start", help="Start from this province code (inclusive)")
    parser.add_argument("--skip", nargs="*", default=[], help="Province codes to skip")
    parser.add_argument("--only", nargs="*", help="Only these province codes")
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--skip-discover", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip provinces already in progress file")
    return parser.parse_args()


def load_progress() -> dict:
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"done": {}, "order": []}


def save_progress(data: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_summary_json(stdout: str) -> dict:
    start = stdout.find("{")
    if start == -1:
        return {}
    try:
        payload, _end = json.JSONDecoder().raw_decode(stdout[start:])
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    skip = set(args.skip or [])
    skip.add("guangdong")  # already done with known overrides

    if args.only:
        codes = [c for c in args.only if c in PROVINCE_CODES]
    else:
        codes = list(PROVINCE_CODES)
        if args.start:
            if args.start not in codes:
                raise SystemExit(f"Unknown start code: {args.start}")
            codes = codes[codes.index(args.start) :]

    progress = load_progress()
    if args.resume:
        skip.update(progress.get("done", {}).keys())
    codes = [c for c in codes if c not in skip]

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log_fh:
        for code in codes:
            logging.info("======== %s ========", code)
            log_fh.write(f"\n======== {code} ========\n")
            cmd = [
                sys.executable,
                str(ROOT / "scripts" / "crawl_province.py"),
                code,
                "--delay",
                str(args.delay),
            ]
            if args.skip_discover:
                cmd.append("--skip-discover")
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            parsed = parse_summary_json(stdout)
            result = {
                "code": code,
                "exit_code": proc.returncode,
                **parsed,
                "stderr_tail": "\n".join(stderr.strip().splitlines()[-30:]),
            }
            progress["done"][code] = {
                "exit_code": proc.returncode,
                "leaders": result.get("leaders"),
                "events": result.get("events"),
                "leaders_failed": result.get("leaders_failed"),
                "appointments_failed": result.get("appointments_failed"),
            }
            if code not in progress["order"]:
                progress["order"].append(code)
            save_progress(progress)
            with SUMMARY.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            log_fh.write(stdout)
            log_fh.write(stderr)
            log_fh.flush()
            logging.info(
                "Finished %s exit=%s leaders=%s events=%s",
                code,
                proc.returncode,
                result.get("leaders"),
                result.get("events"),
            )


if __name__ == "__main__":
    main()
