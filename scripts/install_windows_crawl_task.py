# -*- coding: utf-8 -*-
"""Install / remove Windows Task Scheduler job for appointment crawl.

Creates a daily 03:30 task. If the PC was off at that time,
``StartWhenAvailable`` makes Windows run it soon after the next boot/login.

Examples::

    python scripts/install_windows_crawl_task.py
    python scripts/install_windows_crawl_task.py --remove
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
TASK_NAME = "TaxHR_AppointmentsDueCrawl"
SCRIPT = ROOT / "scripts" / "crawl_due_appointments.py"
DB = ROOT / "output" / "tax_hr.db"
LOG = ROOT / "output" / "crawl_due_appointments.log"


def _python_exe() -> str:
    return sys.executable


def _cmd_line() -> str:
    py = _python_exe()
    # Task Scheduler "command" + arguments: run via cmd so redirect works.
    return (
        f'cmd.exe /c "cd /d {ROOT} && \\"{py}\\" \\"{SCRIPT}\\" --db \\"{DB}\\" '
        f'>> \\"{LOG}\\" 2>&1"'
    )


def _task_xml(hour: int, minute: int) -> str:
    start = f"2026-01-01T{hour:02d}:{minute:02d}:00"
    cmd = escape(_cmd_line())
    # StartWhenAvailable: missed schedule → run after next boot when PC is on.
    # DisallowStartIfOnBatteries/StopIfGoingOnBatteries off so laptops still run.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>税局人事：到期任免公告增量爬取并入库（各级默认 7 天）</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c "cd /d {escape(str(ROOT))} &amp;&amp; &quot;{escape(_python_exe())}&quot; &quot;{escape(str(SCRIPT))}&quot; --db &quot;{escape(str(DB))}&quot; &gt;&gt; &quot;{escape(str(LOG))}&quot; 2&gt;&amp;1"</Arguments>
      <WorkingDirectory>{escape(str(ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(hour: int = 3, minute: int = 30) -> None:
    if not SCRIPT.is_file():
        raise SystemExit(f"missing crawl script: {SCRIPT}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    schtasks = shutil.which("schtasks")
    if not schtasks:
        raise SystemExit("schtasks not found (Windows Task Scheduler required)")

    subprocess.run(
        [schtasks, "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )

    with tempfile.NamedTemporaryFile(
        "w", suffix=".xml", delete=False, encoding="utf-16"
    ) as tmp:
        tmp.write(_task_xml(hour, minute))
        xml_path = tmp.name

    try:
        create = subprocess.run(
            [schtasks, "/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(xml_path).unlink(missing_ok=True)

    if create.returncode != 0:
        print(create.stdout)
        print(create.stderr, file=sys.stderr)
        raise SystemExit(
            "创建计划任务失败。若提示拒绝访问，请用「以管理员身份运行」的终端再执行一次。"
        )
    print(f"已创建计划任务: {TASK_NAME}")
    print(f"  触发: 每天 {hour:02d}:{minute:02d}")
    print("  错过补跑: 开机后尽快执行（StartWhenAvailable）")
    print(f"  命令: {SCRIPT.name}（仅爬到期站点，各级 7 天一轮）")
    print(f"  日志: {LOG}")
    print("说明: 电脑长期关机期间不会爬；下次开机后会补跑错过的任务。")


def remove() -> None:
    schtasks = shutil.which("schtasks")
    if not schtasks:
        raise SystemExit("schtasks not found")
    result = subprocess.run(
        [schtasks, "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout or result.stderr or "删除失败（可能任务不存在）")
        raise SystemExit(result.returncode)
    print(f"已删除计划任务: {TASK_NAME}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="Remove the scheduled task")
    parser.add_argument("--hour", type=int, default=3)
    parser.add_argument("--minute", type=int, default=30)
    args = parser.parse_args()
    if args.remove:
        remove()
    else:
        install(hour=args.hour, minute=args.minute)


if __name__ == "__main__":
    main()
