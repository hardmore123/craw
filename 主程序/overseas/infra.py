"""基础设施：域名级限速、反爬识别、磁盘缓存、代理解析。

这几块是从 cert_verify 的实战经验搬过来并加强的：
- RateLimiter：借鉴 Crawl4AI async_dispatcher 的思路，按域名限速并对
  429/503 做指数退避（cert_verify 原来是全局固定 sleep，粒度太粗）。
- BlockDetector：借鉴 Crawl4AI antibot_detector，除关键字外加结构性判断，
  避免"HTTP 200 但其实是验证码页"被当成正常结果。
- resolve_proxy：沿用 cert_verify v2.6 的教训——探测到死代理必须自动直连。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import threading
import time
import urllib.parse

from . import config

# ---------------------------------------------------------------- 限速


class RateLimiter:
    """按域名限速：最小间隔 + 随机抖动，并根据响应状态动态退避。

    线程安全，可在多线程抓取时共享一个实例。
    """

    def __init__(self, min_interval: float | None = None, jitter: float | None = None,
                 max_delay: float = 120.0):
        self.min_interval = config.MIN_INTERVAL if min_interval is None else min_interval
        self.jitter = config.JITTER if jitter is None else jitter
        self.max_delay = max_delay
        self._last: dict[str, float] = {}
        self._factor: dict[str, float] = {}      # 域名 -> 退避倍数
        self._lock = threading.Lock()

    @staticmethod
    def domain(url: str) -> str:
        try:
            return urllib.parse.urlparse(url).netloc.lower() or "-"
        except ValueError:
            return "-"

    def wait(self, url: str) -> float:
        """必要时阻塞等待，返回实际等待秒数。"""
        d = self.domain(url)
        with self._lock:
            last = self._last.get(d, 0.0)
            factor = self._factor.get(d, 1.0)
        need = self.min_interval * factor + random.uniform(0, self.jitter)
        elapsed = time.time() - last
        slept = 0.0
        if last and elapsed < need:
            slept = need - elapsed
            time.sleep(slept)
        with self._lock:
            self._last[d] = time.time()
        return slept

    def feedback(self, url: str, status: int) -> None:
        """按响应状态调整该域名的退避倍数。"""
        d = self.domain(url)
        with self._lock:
            f = self._factor.get(d, 1.0)
            if status in (429, 503) or status == -2:      # -2 = 被识别为反爬
                f = min(f * 2.0, self.max_delay / max(self.min_interval, 0.1))
            elif 200 <= status < 300:
                f = max(1.0, f * 0.8)                     # 逐步恢复
            self._factor[d] = f

    def state(self) -> dict:
        with self._lock:
            return {d: round(f, 2) for d, f in self._factor.items()}


# ---------------------------------------------------------------- 反爬识别

# 明确的反爬/验证码/拒绝特征
_BLOCK_MARKERS = (
    # Cloudflare
    "just a moment", "attention required", "cf-browser-verification",
    "checking your browser", "cf-challenge",
    # Amazon
    "enter the characters you see below",
    "type the characters you see in this image",
    "sorry! something went wrong",
    "api-services-support@amazon.com",
    # 通用
    "access denied", "request blocked", "you don't have permission",
    "are you a robot", "robot or human", "unusual traffic", "captcha",
    "bot-message", "activate and hold",
    # PerimeterX 人机挑战页的**标题**（2026-09-20 补）：walmart_ca/walmart_mx/
    # sams_mx 的挑战页标题分别是 "Verify Your Identity" / "Verifica tu identidad"。
    # 这两条必须单独存在，因为挑战页同时带 `px-captcha` 标记，而该标记**包含
    # `captcha` 子串** —— 任何站点一旦豁免 `captcha`（Shopify/Costco 类正常页含
    # 该字样的站都需要豁免），PX 挑战就会被一起豁免掉、判成 `blocked=False`，
    # 最终被记成 `no_item`（静默丢数据）。有这两条，豁免才是安全的。
    "verify your identity", "verifica tu identidad",
    # Best Buy 美国站：非美国出口 IP **不是 403**，而是 HTTP 200 + 国际选择页。
    # 不加这条标记就只会表现为"搜索页 0 候选"，被误当成选择器失效。
    "best buy international",
    # Akamai 挑战页（2026-09-20 补）：Best Buy 等站的 Akamai 拒绝页文案是
    # "Pardon Our Interruption"，原文案表里没有这条 → 一旦对方改用 Akamai 挑战
    # 就会**漏判**（页面表现为"0 候选"，被误当成选择器失效）。
    "pardon our interruption",
    # 企业出口防火墙（cert_verify 踩过：HTTP 200 返回自制拦截页）
    "url过滤", "not allowed to access this website", "/disable/disable.htm",
)

# ---------------------------------------------------------------- 登录墙识别
#
# 与反爬拦截必须分开：登录墙是"会话无效"，重试/退避/站级熔断都治不好，
# 只会把一个可修的会话问题伪装成"站点被封"。历史事故：Amazon 跳登录页
# 导致缺锚点 → 被判 no_anchor_element → 触发站级熔断，621 型号静默全跳过。
#
# 判据优先级：明确 HTTP 反爬码(403/429/503) > 登录墙 > 反爬关键字 > 结构异常。
_LOGIN_URL_MARKERS = (
    "/ap/signin", "/gp/signin", "/signin", "/login",
    "/account/login", "/auth/", "signin?", "openid",
    "return_to=", "/register",
)

# 仅在"标题命中"或"正文命中且页面很短"时才算登录墙，避免商品页里
# 出现 "Sign in" 字样（页头 Account 菜单）被误判。
_LOGIN_TITLE_MARKERS = ("sign in", "log in", "login", "登录", "登入", "iniciar sesión")

_LOGIN_BODY_MARKERS = (
    "enter your mobile number or email",
    "enter your email or mobile phone number",
    "password",
    "create account",
    "forgot your password",
)


def login_wall_hit(final_url: str, title: str, html: str) -> bool:
    """判断当前页面是否为登录/注册墙。

    判据（任一命中即算），按可靠性排序：
      1) 最终 URL 落在登录路径 —— 跳转是最可靠的信号
      2) 标题**几乎等于**登录词 + 页面很轻 —— 真正的登录页总是又短又干净
      3) 正文含登录表单特征 且 页面很轻 —— 兜底

    "页面很轻"（< 20KB）是判据 2/3 的前提：商品页常含 "Sign in" 页头菜单，
    标题也可能以 Login/Sign in 开头（如 "Login TV Stand 55 inch"），
    仅凭文字匹配必然误伤；而登录页永远不会是几百 KB 的大页面。
    """
    url = (final_url or "").lower()
    if url and any(m in url for m in _LOGIN_URL_MARKERS):
        return True
    tiny = bool(html) and len(html) < 20000
    t = " ".join((title or "").strip().lower().split())
    if tiny and t:
        for m in _LOGIN_TITLE_MARKERS:
            if t == m:
                return True
            rest = t[len(m):].strip(" \t-–—:|·") if t.startswith(m) else None
            # 允许极少量修饰词（"Sign in to Amazon"），但必须有界
            if rest is not None and len(rest) <= 20 and len(rest.split()) <= 2:
                return True
    if tiny:
        low = html.lower()
        if any(m in low for m in _LOGIN_BODY_MARKERS):
            return True
    return False


class BlockDetector:
    """判断响应是否为反爬/拦截页。

    单看 HTTP 状态码不够：很多站点用 200 返回验证码页。所以三路判断：
    1) 状态码信号（403/429/503）
    2) 关键字特征
    3) 结构性：正文极短或缺少业务锚点元素
    """

    def __init__(self, min_html_len: int = 1500, ignore_markers=None):
        self.min_html_len = min_html_len
        # 部分站点正常页面会内嵌某些 marker 文本（如 Samsung 页面前段引用 captcha
        # 脚本）导致误判。适配器可声明豁免这些关键字，仅跳过关键字匹配这一路，
        # HTTP 403/429/503、正文过短、缺锚点等其他判据不受影响。
        self.ignore_markers = {m.lower() for m in (ignore_markers or ())}

    def check(self, status: int, html: str = "", title: str = "",
              anchor_present: bool | None = None,
              final_url: str = "") -> tuple[bool, str]:
        """返回 (是否拦截, 原因)。

        原因取值约定（调用方据此区分"退避重试"与"提示补登录态"）：
          http_403/429/503   → 明确反爬
          not_logged_in      → 会话失效/未登录（非反爬，不应计入站级熔断）
          marker:*           → 反爬关键字
          too_short:N        → 正文过短
          no_anchor_element  → 缺业务锚点（疑似改版/软拦截）
        """
        if status in (403, 429, 503):
            return True, f"http_{status}"
        # 登录墙必须先于关键字与锚点判断：登录页又短又无业务锚点，
        # 否则会被误报成反爬，把可修的会话问题伪装成"站点被封"。
        if login_wall_hit(final_url, title, html):
            return True, "not_logged_in"
        low = (title + "\n" + (html[:8000] if html else "")).lower()
        for m in _BLOCK_MARKERS:
            if m in self.ignore_markers:
                continue
            if m in low:
                return True, f"marker:{m[:32]}"
        if html and len(html) < self.min_html_len:
            return True, f"too_short:{len(html)}"
        # 页面能打开、但业务锚点元素一个都没有 → 大概率是软性拦截或改版
        if anchor_present is False:
            return True, "no_anchor_element"
        return False, ""


# ---------------------------------------------------------------- 磁盘缓存


class DiskCache:
    """URL -> 响应正文 的磁盘缓存，带 TTL。开发调试时可大幅减少真实请求。"""

    def __init__(self, cache_dir: str | None = None, ttl: float | None = None,
                 enabled: bool | None = None):
        self.dir = cache_dir or config.CACHE_DIR
        self.ttl = config.CACHE_TTL if ttl is None else ttl
        self.enabled = config.CACHE_ENABLED if enabled is None else enabled
        if self.enabled:
            os.makedirs(self.dir, exist_ok=True)

    def _path(self, key: str) -> str:
        h = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()
        return os.path.join(self.dir, f"{h}.json")

    def get(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        p = self._path(key)
        try:
            if time.time() - os.path.getmtime(p) > self.ttl:
                return None
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, payload: dict) -> None:
        if not self.enabled:
            return
        try:
            with open(self._path(key), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError:
            pass

    def clear(self) -> int:
        n = 0
        if not os.path.isdir(self.dir):
            return 0
        for name in os.listdir(self.dir):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(self.dir, name))
                    n += 1
                except OSError:
                    pass
        return n


# ---------------------------------------------------------------- 代理


def _alive(server: str, timeout: float = 3.0) -> bool:
    """TCP 探活：只看端口是否可连，避免用一个已死的代理导致全部失败。"""
    try:
        u = urllib.parse.urlparse(server if "://" in server else "http://" + server)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def resolve_proxy(explicit: str = "") -> str:
    """按优先级解析代理，并做探活；不可用则返回空串（直连）。

    优先级：显式参数 > OVERSEAS_PROXY > HTTPS_PROXY/HTTP_PROXY/ALL_PROXY
    """
    candidates = [explicit, config.PROXY]
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        candidates.append(os.environ.get(k, ""))
    for c in candidates:
        c = (c or "").strip()
        if not c:
            continue
        if not config.PROXY_PROBE or _alive(c):
            return c
    return ""
