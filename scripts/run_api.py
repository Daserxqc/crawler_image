"""Run the public search API (PR7–PR8)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Listen address (0.0.0.0 = LAN)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    from tax_platform.paths import ensure_project_cwd

    ensure_project_cwd()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("请先安装依赖: pip install fastapi uvicorn openpyxl") from exc

    if args.host in {"0.0.0.0", "::"}:
        print(f"Listening on all interfaces — LAN URL: http://<本机局域网IP>:{args.port}/")
        print("本机请打开: http://127.0.0.1:{}/".format(args.port))
        print("不要用 http://0.0.0.0:{}/ 访问（仅监听地址，浏览器缩放会异常变大）".format(args.port))

    uvicorn.run(
        "tax_platform.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
