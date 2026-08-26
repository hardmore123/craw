"""python -m cert_verify.web —— 启动本地网页服务。"""
from __future__ import annotations

import argparse
import sys

from .server import serve

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description="证书核对本地网页服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
