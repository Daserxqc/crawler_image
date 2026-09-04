# -*- coding: utf-8 -*-
"""Build a handoff folder (+ zip) for deploying on another machine / Aliyun ECS.

Includes cleaned source (optional), required data files, env template, and docs.

Examples::

    python scripts/pack_handoff.py
    python scripts/pack_handoff.py --no-source   # data + docs only
    python scripts/pack_handoff.py --out D:/share/tax-hr-handoff
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_REQUIRED = ("tax_hr.db",)
DATA_RECOMMENDED = (
    "city_sites_registry.json",
    "crawl_state.json",
)

SOURCE_IGNORE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "node_modules",
    ".cursor",
    "output",
}
SOURCE_IGNORE_FILE_SUFFIXES = {".pyc", ".pyo", ".log"}


def _ignore_source(dir_path: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in SOURCE_IGNORE_DIR_NAMES:
            ignored.add(name)
            continue
        if any(name.endswith(suf) for suf in SOURCE_IGNORE_FILE_SUFFIXES):
            ignored.add(name)
    return ignored


def _copy_data(dest_data: Path) -> list[str]:
    dest_data.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    src_out = ROOT / "output"
    for name in DATA_REQUIRED:
        src = src_out / name
        if not src.is_file():
            raise SystemExit(f"缺少必交文件: {src}")
        shutil.copy2(src, dest_data / name)
        notes.append(f"OK  data/{name} ({src.stat().st_size // (1024 * 1024)} MB)")
    for name in DATA_RECOMMENDED:
        src = src_out / name
        if src.is_file():
            shutil.copy2(src, dest_data / name)
            notes.append(f"OK  data/{name}")
        else:
            notes.append(f"MISS data/{name}（建议补上后再打包）")
    return notes


def _write_env_example(path: Path) -> None:
    path.write_text(
        """# Copy to /etc/tax-hr.env (chmod 600) or export before start.
# Generate secret: openssl rand -hex 32

TAX_HR_SECRET=change-me-to-a-long-random-string
TAX_HR_ADMIN_USER=admin
TAX_HR_ADMIN_PASSWORD=change-me-strong-password
TAX_HR_PUBLIC_BASE=http://YOUR_PUBLIC_IP_OR_DOMAIN

# Optional overrides (defaults resolve under the project root):
# TAX_HR_DB=/opt/crawler_image/output/tax_hr.db
# TAX_HR_OUTPUT_DIR=/opt/crawler_image/output
# TAX_HR_CHROME=/usr/bin/google-chrome-stable

# Optional mail:
# SMTP_HOST=
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASS=
# SMTP_FROM=
""".lstrip(),
        encoding="utf-8",
    )


def _write_readme(path: Path, *, with_source: bool) -> None:
    lines = [
        "税局人事检索 — 交付包",
        "=" * 40,
        "",
        "本包内容：",
        "- data/tax_hr.db              必装主库（GitHub 没有）",
        "- data/city_sites_registry.json  市县站点（继续爬取需要）",
        "- data/crawl_state.json       抓取调度状态（建议）",
        "- env.example                 环境变量模板",
        "- docs/deploy-aliyun.md       阿里云逐步部署",
        "- docs/handoff-package.md     交付说明",
    ]
    if with_source:
        lines.append("- source/                     精简源码（无 .venv / 无杂乱 output）")
    lines.extend(
        [
            "",
            "对方机器上建议步骤：",
            "1. 安装 Python 3.10+，见 docs/deploy-aliyun.md",
            "2. 代码：用 Git 拉最新，或使用本包 source/",
            "3. mkdir -p output && cp data/* output/",
            "4. python3 -m venv .venv && source .venv/bin/activate",
            "5. pip install -r requirements.txt",
            "6. 按 env.example 配置 TAX_HR_*",
            "7. python scripts/run_api.py",
            "8. 每周自动抓取：python scripts/install_linux_crawl_cron.py --install",
            "",
            "不要把浏览器地址写成 http://0.0.0.0:8000/",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output folder (default: dist/tax-hr-handoff-YYYYMMDD)",
    )
    parser.add_argument(
        "--no-source",
        action="store_true",
        help="Only pack data + docs (assume peer clones git)",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Do not create .zip alongside the folder",
    )
    args = parser.parse_args()

    stamp = date.today().isoformat()
    out = args.out or (ROOT / "dist" / f"tax-hr-handoff-{stamp}")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    notes = _copy_data(out / "data")
    _write_env_example(out / "env.example")
    docs_dest = out / "docs"
    docs_dest.mkdir(parents=True, exist_ok=True)
    for name in ("deploy-aliyun.md", "handoff-package.md"):
        src = ROOT / "docs" / name
        if src.is_file():
            shutil.copy2(src, docs_dest / name)

    with_source = not args.no_source
    if with_source:
        shutil.copytree(
            ROOT,
            out / "source",
            ignore=_ignore_source,
            dirs_exist_ok=False,
        )
        # Ensure empty output dir exists in source tree for first run
        (out / "source" / "output").mkdir(exist_ok=True)
        (out / "source" / "output" / ".gitkeep").write_text("", encoding="utf-8")

    _write_readme(out / "README-交接.txt", with_source=with_source)
    manifest = out / "MANIFEST.txt"
    manifest.write_text("\n".join(notes) + "\n", encoding="utf-8")

    zip_path = None
    if not args.no_zip:
        zip_path = out.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file in out.rglob("*"):
                if file.is_file():
                    zf.write(file, arcname=str(file.relative_to(out.parent)))

    print(f"Handoff folder: {out}")
    for line in notes:
        print(" ", line)
    if zip_path:
        print(f"Zip: {zip_path} ({zip_path.stat().st_size // (1024 * 1024)} MB)")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
