# -*- coding: utf-8 -*-
"""Install / print a Linux cron line for weekly-ish due appointment crawl.

Cloud (Aliyun ECS) counterpart of ``install_windows_crawl_task.py``.
Cadence in code is still ~7 days per site; running the checker **daily**
is recommended so due sites are picked up promptly.

Examples::

    python scripts/install_linux_crawl_cron.py           # print + optional install
    python scripts/install_linux_crawl_cron.py --install
    python scripts/install_linux_crawl_cron.py --remove
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "crawl_due_appointments.py"
DB = ROOT / "output" / "tax_hr.db"
LOG = ROOT / "output" / "crawl_due_appointments.log"
MARKER = "# TaxHR_AppointmentsDueCrawl"


def _python_exe() -> str:
    return sys.executable


def _cron_line(hour: int, minute: int) -> str:
    py = _python_exe()
    # cd to ROOT so any residual relative paths still work; script also
    # ensure_project_cwd() and resolves DB under PROJECT_ROOT.
    return (
        f"{minute} {hour} * * * cd {ROOT} && "
        f'"{py}" "{SCRIPT}" --db "{DB}" '
        f">> {LOG} 2>&1 {MARKER}"
    )


def _current_crontab() -> str:
    try:
        out = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError:
        return ""
    except FileNotFoundError as exc:
        raise SystemExit("crontab not found; install cron (e.g. apt install cron)") from exc


def _write_crontab(body: str) -> None:
    proc = subprocess.run(["crontab", "-"], input=body.encode("utf-8"), check=False)
    if proc.returncode != 0:
        raise SystemExit("failed to write crontab")


def install(hour: int, minute: int) -> None:
    if not shutil.which("crontab"):
        raise SystemExit("crontab not found")
    line = _cron_line(hour, minute)
    existing = _current_crontab()
    lines = [ln for ln in existing.splitlines() if MARKER not in ln]
    lines.append(line)
    body = "\n".join(lines).rstrip() + "\n"
    _write_crontab(body)
    print("Installed cron entry:")
    print(line)


def remove() -> None:
    existing = _current_crontab()
    lines = [ln for ln in existing.splitlines() if MARKER not in ln]
    body = ("\n".join(lines).rstrip() + "\n") if lines else ""
    _write_crontab(body)
    print("Removed TaxHR crawl cron entries (if any).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hour", type=int, default=3)
    parser.add_argument("--minute", type=int, default=30)
    parser.add_argument("--install", action="store_true", help="Write into current user crontab")
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    if args.remove:
        remove()
        return
    line = _cron_line(args.hour, args.minute)
    print("Suggested crontab line (daily checker; sites refresh ~every 7 days):\n")
    print(line)
    print()
    if args.install:
        install(args.hour, args.minute)
    else:
        print("Dry-run only. Re-run with --install to add it, or paste into: crontab -e")


if __name__ == "__main__":
    main()
