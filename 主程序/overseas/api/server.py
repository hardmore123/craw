"""HTTP 服务入口：标准库 http.server，零第三方依赖。

启动：
    python -m overseas.api.server                       # 127.0.0.1:8000
    python -m overseas.api.server --host 0.0.0.0 --port 8080
    python -m overseas.api.server --cors "*"            # 放开跨域（本地前端联调）

端点（全部返回 JSON）：
    GET  /api/health                        健康检查 + 库统计
    GET  /api/brands                        六品牌概览
    GET  /api/models?brand=&year=&limit=&offset=        型号清单（支持上市年份筛选）
    GET  /api/spec?brand=&release=1         SPEC 宽表（含上市时间行）
    GET  /api/prices?brand=&model=&year=    各机型最新店铺报价 + 最低价
    GET  /api/prices/history?product_id=&shop=          价格时序
    GET  /api/reviews?brand=&model=&product_id=&limit=&offset=   评论分页
    GET  /api/status?task=&brand=&state=&week=          采集状态（SPEC + kakaku）
    GET  /api/jobs                          任务列表 + 队列信息
    POST /api/jobs                          提交采集任务 {"task":"price","params":{...}}
    GET  /api/jobs/<id>                     单任务状态
    GET  /api/jobs/<id>/log?offset=         增量日志
    POST /api/jobs/<id>/cancel              取消任务

注意：**默认无鉴权**，仅监听 127.0.0.1。若要对外暴露（--host 0.0.0.0），
必须在前面加反向代理做认证与限流，见 项目总说明.md §7。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from urllib.parse import quote

from .. import __version__, config
from ..price_export import SHOP_COLUMN_NAMES, SHOP_COLUMN_ZH
from . import exporter
from . import regions as regions_mod
from ..db import Database
from .jobs import TASK_BUILDERS, manager, scheduler
from .probe import probe_url
from .service import Service

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 运行期配置（由 main 注入）
SETTINGS = {"db": None, "cors": "", "max_limit": 5000}


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")


def _int_arg(query: dict, name: str, default=None):
    raw = (query.get(name) or [""])[0].strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"参数 {name} 必须是整数，收到 {raw!r}")


def _str_arg(query: dict, name: str, default: str = "") -> str:
    return (query.get(name) or [default])[0].strip()


def _bool_arg(query: dict, name: str, default: bool) -> bool:
    raw = (query.get(name) or [""])[0].strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


class Handler(BaseHTTPRequestHandler):
    server_version = f"overseas-api/{__version__}"

    # ---------------- 基础响应 ----------------

    def _send(self, status: int, payload) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        cors = SETTINGS.get("cors") or ""
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"error": message, "status": status})

    def _send_file(self, data: bytes, filename: str, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        # filename* 用 RFC5987 编码，中文文件名不乱码
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"download\"; "
                         f"filename*=UTF-8''{quote(filename)}")
        self.send_header("Content-Length", str(len(data)))
        cors = SETTINGS.get("cors") or ""
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:      # 精简访问日志
        sys.stderr.write("[api] %s - %s\n" % (self.address_string(), fmt % args))

    def do_OPTIONS(self) -> None:                        # noqa: N802
        self._send(204, {})

    # ---------------- 路由 ----------------

    def do_GET(self) -> None:                            # noqa: N802
        parsed = urlparse(self.path)
        raw_path = parsed.path
        query = parse_qs(parsed.query)
        # 非 /api 路径走静态前端（单页应用）
        if not raw_path.startswith("/api"):
            self._serve_static(raw_path)
            return
        path = raw_path.rstrip("/") or "/"
        try:
            self._route_get(path, query)
        except ValueError as e:
            self._error(400, str(e))
        except KeyError as e:
            self._error(404, f"不存在: {e}")
        except Exception as e:
            traceback.print_exc()
            self._error(500, f"{type(e).__name__}: {e}")

    def _serve_static(self, raw_path: str) -> None:
        """服务 static/ 下的前端文件；根路径与未知路径回退 index.html（SPA）。"""
        rel = raw_path.lstrip("/") or "index.html"
        # 防目录穿越
        safe = os.path.normpath(rel).replace("\\", "/")
        if safe.startswith("..") or safe.startswith("/"):
            self._error(403, "forbidden")
            return
        full = os.path.join(STATIC_DIR, safe)
        if not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "index.html")
        if not os.path.isfile(full):
            self._error(404, "前端未构建：static/index.html 不存在")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as handle:
                body = handle.read()
        except OSError as e:
            self._error(500, str(e))
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8"
                         if ctype.startswith("text/") or "javascript" in ctype
                         or "json" in ctype else ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_POST(self) -> None:                           # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            self._route_post(path, body)
        except json.JSONDecodeError as e:
            self._error(400, f"JSON 解析失败: {e}")
        except ValueError as e:
            self._error(400, str(e))
        except KeyError as e:
            self._error(404, f"不存在: {e}")
        except Exception as e:
            traceback.print_exc()
            self._error(500, f"{type(e).__name__}: {e}")

    # ---------------- GET 分发 ----------------

    def _route_get(self, path: str, query: dict) -> None:
        max_limit = int(SETTINGS["max_limit"])
        limit = min(_int_arg(query, "limit", 200) or 200, max_limit)
        offset = max(_int_arg(query, "offset", 0) or 0, 0)
        brand = _str_arg(query, "brand")
        model = _str_arg(query, "model")
        year = _int_arg(query, "year")
        region = (_str_arg(query, "region") or "jp").lower()

        if path == "/" or path == "/api":
            self._send(200, {
                "service": "overseas-api", "version": __version__,
                "endpoints": [
                    "/api/health", "/api/brands", "/api/regions", "/api/models",
                    "/api/spec", "/api/prices", "/api/prices/history",
                    "/api/prices/weekly", "/api/reviews",
                    "/api/status", "/api/shop-aliases", "/api/probe", "/api/schedules",
                    "/api/export/spec", "/api/export/reviews", "/api/export/prices",
                    "/api/jobs", "/api/jobs/<id>", "/api/jobs/<id>/log",
                ],
                "tasks": sorted(TASK_BUILDERS),
            })
            return

        # 地区元数据
        if path == "/api/regions":
            self._send(200, {"items": regions_mod.REGIONS})
            return

        # 通道验证：探测目标站点是否可访问/是否被拦
        if path == "/api/probe":
            url = _str_arg(query, "url")
            use_browser = _bool_arg(query, "browser", False)
            if url:
                self._send(200, probe_url(url, use_browser=use_browser))
            else:
                # 不给 url 时探测某地区（或全部）所有目标站点
                targets = regions_mod.all_probe_targets(_str_arg(query, "region"))
                results = [{**t, **probe_url(t["url"], use_browser=use_browser)}
                           for t in targets]
                self._send(200, {"items": results})
            return

        # 定时任务列表
        if path == "/api/schedules":
            self._send(200, {"items": scheduler.list()})
            return

        # 任务相关（不需要 DB 连接）
        if path == "/api/jobs":
            self._send(200, {"queue": manager.queue_info(),
                             "items": manager.list(limit=limit)})
            return
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{6,32})", path)
        if m:
            job = manager.get(m.group(1))
            if job is None:
                raise KeyError(m.group(1))
            self._send(200, job.to_dict())
            return
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{6,32})/log", path)
        if m:
            self._send(200, manager.log(m.group(1),
                                        offset=_int_arg(query, "offset", 0) or 0))
            return

        # 导出下载：/api/export/<kind>?brand=&format=xlsx|csv
        m = re.fullmatch(r"/api/export/(spec|reviews|prices)", path)
        if m:
            kind = m.group(1)
            fmt = _str_arg(query, "format") or "xlsx"
            if fmt not in ("xlsx", "csv"):
                raise ValueError("format 只能是 xlsx 或 csv")
            if kind == "spec" and not brand:
                raise ValueError("导出 SPEC 需要 brand")
            db = Database(SETTINGS["db"])
            try:
                db.init_schema()
                if kind == "spec":
                    data, fname, ctype = exporter.export_spec(
                        db, brand, fmt, SETTINGS["db"], region=region)
                elif kind == "reviews":
                    # 默认不现场翻译：整库翻译耗时长会让下载超时。需要译文时
                    # 传 translate=1，或用回填脚本生成「已翻译」文件。
                    data, fname, ctype = exporter.export_reviews(
                        db, brand, fmt,
                        translate=_bool_arg(query, "translate", False),
                        engine=_str_arg(query, "engine") or "nllb",
                        region=region,
                    )
                else:
                    data, fname, ctype = exporter.export_prices(
                        db, brand, fmt, region=region)
            finally:
                db.close()
            self._send_file(data, fname, ctype)
            return

        # 数据查询（每请求一个 Service，避免 SQLite 跨线程复用）
        with Service(SETTINGS["db"]) as svc:
            if path == "/api/health":
                self._send(200, svc.health())
            elif path == "/api/brands":
                self._send(200, {"items": svc.brands(region=region)})
            elif path == "/api/models":
                self._send(200, svc.models(brand=brand, year=year,
                                           limit=limit, offset=offset,
                                           region=region))
            elif path == "/api/spec":
                if not brand:
                    raise ValueError("缺少参数 brand")
                self._send(200, svc.spec(
                    brand, with_release=_bool_arg(query, "release", True),
                    region=region))
            elif path == "/api/prices":
                self._send(200, svc.prices(brand=brand, model=model, year=year,
                                           limit=limit, offset=offset,
                                           region=region))
            elif path == "/api/prices/history":
                pid = _int_arg(query, "product_id")
                if not pid:
                    raise ValueError("缺少参数 product_id")
                self._send(200, svc.price_history(
                    pid, shop=_str_arg(query, "shop"), limit=limit, region=region))
            elif path == "/api/prices/weekly":
                weeks = _int_arg(query, "weeks") or 12
                self._send(200, svc.weekly_price_trend(
                    brand=brand, model=model, weeks=weeks, region=region))
            elif path == "/api/reviews":
                if region != "jp" and year is not None:
                    raise ValueError("非日本评价查询不支持 year；加拿大线没有发售年份筛选")
                self._send(200, svc.reviews(brand=brand, model=model,
                                            product_id=_int_arg(query, "product_id"),
                                            limit=limit, offset=offset,
                                            region=region))
            elif path == "/api/status":
                self._send(200, svc.status(task=_str_arg(query, "task"), brand=brand,
                                           state=_str_arg(query, "state"),
                                           week=_str_arg(query, "week"),
                                           limit=limit, offset=offset,
                                           region=region))
            elif path == "/api/shop-aliases":
                # 价格导出店铺列的日文原名 → 中文译名（只读展示，与导出表头同源）。
                self._send(200, {"items": [
                    {"original": name, "zh": SHOP_COLUMN_ZH.get(name, name)}
                    for name in SHOP_COLUMN_NAMES
                ]})
            else:
                self._error(404, f"未知端点: {path}")

    # ---------------- POST 分发 ----------------

    def _route_post(self, path: str, body: dict) -> None:
        if path == "/api/jobs":
            task = str(body.get("task") or "").strip()
            if not task:
                raise ValueError("缺少字段 task")
            params = body.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params 必须是 JSON 对象")
            job = manager.submit(task, params)
            self._send(202, job.to_dict())
            return
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{6,32})/cancel", path)
        if m:
            ok = manager.cancel(m.group(1))
            self._send(200 if ok else 409,
                       {"id": m.group(1), "cancelled": ok})
            return

        # 定时任务：新建
        if path == "/api/schedules":
            task = str(body.get("task") or "").strip()
            trigger = body.get("trigger") or {}
            if not task:
                raise ValueError("缺少字段 task")
            if not isinstance(trigger, dict) or not trigger:
                raise ValueError("缺少字段 trigger")
            sch = scheduler.add(task=task, params=body.get("params") or {},
                                trigger=trigger, name=str(body.get("name") or ""),
                                enabled=bool(body.get("enabled", True)))
            self._send(201, sch.to_dict())
            return
        # 定时任务：更新（启停/改规则）
        m = re.fullmatch(r"/api/schedules/([0-9a-f]{6,32})", path)
        if m:
            sch = scheduler.update(m.group(1), **body)
            if sch is None:
                raise KeyError(m.group(1))
            self._send(200, sch.to_dict())
            return
        # 定时任务：删除（用 POST + action=delete，避免实现 DELETE）
        m = re.fullmatch(r"/api/schedules/([0-9a-f]{6,32})/delete", path)
        if m:
            ok = scheduler.remove(m.group(1))
            self._send(200 if ok else 404, {"id": m.group(1), "deleted": ok})
            return
        self._error(404, f"未知端点: {path}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="overseas 后端 API（标准库实现，零依赖）")
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址，默认 127.0.0.1（对外暴露请用反向代理加鉴权）")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db", default=None, help="SQLite 路径，缺省用 config.DB_PATH")
    p.add_argument("--cors", default="", help="Access-Control-Allow-Origin，如 * 或具体源")
    p.add_argument("--max-limit", type=int, default=5000, help="分页 limit 上限")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    SETTINGS["db"] = args.db or config.DB_PATH
    SETTINGS["cors"] = args.cors
    SETTINGS["max_limit"] = args.max_limit
    manager.set_db_path(SETTINGS["db"])

    # 启动前自检数据库可读
    with Service(SETTINGS["db"]) as svc:
        stats = svc.health()["stats"]
    print(f"overseas-api {__version__}")
    print(f"  db     : {SETTINGS['db']}")
    print(f"  stats  : specs={stats['spec_rows']} products={stats['products']} "
          f"reviews={stats['reviews']} shop_prices={stats['price_shop_snapshots']}")
    print(f"  listen : http://{args.host}:{args.port}/api")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  [警告] 监听非本地地址且无内置鉴权，请在前面加反向代理做认证与限流")

    scheduler.start()      # 启动定时任务调度线程
    print(f"  static : {STATIC_DIR}")
    print(f"  schedules: {len(scheduler.list())} 条")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
