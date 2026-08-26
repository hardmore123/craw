"""轻量 HTTP 客户端：只用标准库，带 重试 / 限速 / 磁盘缓存。

为什么不用 requests：本机 Python 是干净环境，少一个依赖少一个坑；
证书站点的查询页都是服务端渲染的静态 HTML，urllib 足够。
需要 JS 渲染或登录的站点（VDE / UL Product iQ）走 browser_client.py。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import random
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

# 查询间隔（礼貌抓取，降低被封风险）：每条之间 15 秒 + 0~5 秒随机抖动。
# VDE / TÜV 走 HttpClient，UL 浏览器会话也用同一组常量（见 providers/ul.py）。
QUERY_MIN_INTERVAL = 15.0
QUERY_JITTER = 5.0

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 企业防火墙拦截页特征（URL 过滤设备会 200 返回一个自制页面）
BLOCK_MARKERS = (
    "URL过滤",
    "not allowed to access this website",
    "/disable/disable.htm",
)


@dataclass
class Response:
    status: int
    url: str
    text: str
    from_cache: bool = False

    @property
    def blocked(self) -> bool:
        if any(m in self.url for m in ("/disable/disable.htm",)):
            return True
        head = self.text[:4000]
        return any(m in head for m in BLOCK_MARKERS)


class HttpClient:
    def __init__(
        self,
        min_interval: float = QUERY_MIN_INTERVAL,
        jitter: float = QUERY_JITTER,
        retries: int = 3,
        timeout: int = 30,
        use_cache: bool = True,
        verify_ssl: bool = True,
        proxy: str | None = None,
    ):
        self.min_interval = min_interval   # 同一 client 的请求最小间隔，礼貌抓取
        self.jitter = jitter               # 间隔随机抖动上限（秒）
        self.retries = retries
        self.timeout = timeout
        self.use_cache = use_cache
        self._last_ts = 0.0
        self._ctx = ssl.create_default_context()
        if not verify_ssl:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE
        # 代理：显式传入 > 环境变量 > 系统代理。开 VPN/加速器后即可穿透企业网关。
        self.proxy = proxy or resolve_proxy()
        handlers = [urllib.request.HTTPSHandler(context=self._ctx)]
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler(
                {"http": self.proxy, "https": self.proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))   # 不继承环境时禁用代理
        self._opener = urllib.request.build_opener(*handlers)
        os.makedirs(CACHE_DIR, exist_ok=True)

    # ---------- 缓存 ----------
    def _cache_path(self, url: str) -> str:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
        return os.path.join(CACHE_DIR, f"{h}.json")

    def _read_cache(self, url: str):
        p = self._cache_path(url)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            return Response(d["status"], d["url"], d["text"], from_cache=True)
        except Exception:
            return None

    def _write_cache(self, url: str, resp: Response) -> None:
        try:
            with open(self._cache_path(url), "w", encoding="utf-8") as f:
                json.dump({"status": resp.status, "url": resp.url, "text": resp.text}, f)
        except Exception:
            pass

    # ---------- 请求 ----------
    def _throttle(self) -> None:
        wait = self.min_interval - (time.time() - self._last_ts)
        if wait > 0:
            time.sleep(wait + random.uniform(0, self.jitter))
        self._last_ts = time.time()

    def get(self, url: str, headers: dict | None = None) -> Response:
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        hdrs = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if headers:
            hdrs.update(headers)

        last_err = ""
        for attempt in range(1, self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, headers=hdrs)
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                    resp = Response(r.status, r.geturl(), raw.decode("utf-8", "replace"))
                if self.use_cache and resp.status == 200 and not resp.blocked:
                    self._write_cache(url, resp)
                return resp
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    if e.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except Exception:
                    pass
                resp = Response(e.code, url, raw.decode("utf-8", "replace"))
                if e.code == 404:          # 404 是"没这个证书"，不重试
                    return resp
                last_err = f"HTTP {e.code}"
                if e.code in (403, 429) or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                return resp
            except Exception as e:  # 网络抖动 / TLS 超时
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(1.5 * attempt)

        return Response(-1, url, f"__FETCH_FAILED__ {last_err}")


def urlencode(params: dict) -> str:
    return urllib.parse.urlencode(params)


def _proxy_alive(proxy_url: str, timeout: float = 1.0) -> bool:
    """探测代理端口是否在监听（能 TCP 连上就算活）。

    解决"VPN 已关，但 Windows 系统代理设置还开着指向已死端口(如 127.0.0.1:7877)"的坑：
    此时应直连，而不是去连一个连不上的代理导致全部失败。
    """
    try:
        p = urllib.parse.urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
        host, port = p.hostname, p.port or 80
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def resolve_proxy() -> str | None:
    """按优先级找一个**存活可用**的代理：

    1. 环境变量 CERT_PROXY / HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
    2. Windows 系统代理（IE/WinINET 设置，开 VPN/加速器后常自动打开）

    找到候选后会**探测端口是否在监听**：活着才返回、用它（VPN 开着的情况）；
    连不上就返回 None → 直连（VPN 关着、但本机已能直接访问官网的情况）。
    返回形如 'http://127.0.0.1:7877'，无可用代理返回 None。
    """
    candidate = None
    for var in ("CERT_PROXY", "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(var)
        if v:
            candidate = v if "://" in v else "http://" + v
            break

    if candidate is None:
        # Windows 系统代理
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enabled:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                if server:
                    # 可能是 "127.0.0.1:7877" 或 "http=...;https=..."
                    if "=" in server:
                        parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
                        server = parts.get("https") or parts.get("http") or ""
                    if server:
                        candidate = server if "://" in server else "http://" + server
        except Exception:
            pass

    if candidate and _proxy_alive(candidate):
        return candidate
    return None            # 无代理或代理已死 → 直连
