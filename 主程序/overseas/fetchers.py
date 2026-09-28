"""抓取层：HttpFetcher（标准库）与 BrowserFetcher（Playwright）。

对上层暴露统一的 Dom 接口，抽取引擎不关心页面是怎么拿到的：
    HttpFetcher    → LxmlDom（装了 lxml+cssselect 时）或仅支持 regex/jsonld
    BrowserFetcher → PlaywrightDom（CSS 选择器原生支持，无需额外依赖）

浏览器实例复用（借鉴 Crawl4AI crawler_pool 思路）：一个 BrowserFetcher
持有一个 browser + context，逐个开关 page，避免每次冷启动浏览器。
"""
from __future__ import annotations

import gzip
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from . import config
from .infra import BlockDetector, DiskCache, RateLimiter, resolve_proxy


_RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


# ★ 增强 stealth JS：隐藏自动化特征，模拟真实浏览器指纹
# 涵盖：webdriver 隐藏、plugins 伪造、languages 伪造、Chrome runtime 注入、
#       permissions API 伪造、WebGL vendor 伪造、Chrome 对象注入
_STEALTH_JS = r"""
// 1. 隐藏 webdriver 标志
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
delete navigator.__proto__.webdriver;

// 2. 伪造 plugins（空 plugins 是 headless 特征）
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
        {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}
    ]
});

// 3. 伪造 languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// 4. 注入 window.chrome 对象（headless 缺失）
if (!window.chrome) {
    window.chrome = {runtime: {}, app: {isInstalled: false}};
}
if (!window.chrome.runtime) {
    window.chrome.runtime = {};
}

// 5. 伪造 permissions API（headless 的 permission.query 行为异常）
const originalQuery = window.navigator.permissions ? window.navigator.permissions.query : null;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : originalQuery(parameters)
    );
}

// 6. 伪造 WebGL vendor/renderer（headless 用 SwiftShader，是强特征）
try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';          // UNMASKED_VENDOR_WEBGL
        if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
        return getParameter.call(this, parameter);
    };
} catch(e) {}

// 7. 隐藏 Playwright 调试特征
if (window.__playwright) delete window.__playwright;
if (window.__pw_manual) delete window.__pw_manual;

// 8. 伪造 hardwareConcurrency 和 deviceMemory
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});

// 9. 伪造 platform（headless 可能是空）
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
"""


def _is_retryable_status(status: int) -> bool:
    return status in _RETRYABLE_HTTP_STATUSES


# lxml + cssselect 是可选依赖：装了就能让纯 HTTP 路径也支持 CSS 选择器
try:
    from lxml import html as _lxml_html      # type: ignore
    _HAS_LXML = True
except ImportError:                          # pragma: no cover
    _HAS_LXML = False


@dataclass
class FetchResult:
    url: str
    status: int = 0
    html: str = ""
    title: str = ""
    from_cache: bool = False
    blocked: bool = False
    block_reason: str = ""
    error: str = ""
    elapsed: float = 0.0
    # 入口页可选择性捕获匹配的网络响应正文（如 LG Coveo 产品搜索结果）。
    response_bodies: list[str] = field(default_factory=list)
    # 入口页遍历的可解释终止信息（如 scroll_stable/click_stable）。
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.blocked and not self.error


# ---------------------------------------------------------------- DOM 适配


class Dom:
    """统一 DOM 访问接口。子类实现具体引擎。"""

    def count(self, selector: str) -> int:
        raise NotImplementedError

    def text(self, selector: str) -> str:
        raise NotImplementedError

    def texts(self, selector: str) -> list[str]:
        raise NotImplementedError

    def attr(self, selector: str, name: str) -> str:
        raise NotImplementedError

    def sub(self, selector: str, limit: int = 200) -> list["Dom"]:
        raise NotImplementedError

    def attr_list(self, selector: str, name: str, limit: int = 60) -> list[str]:
        """匹配选择器的所有元素的某属性值列表（如一条评价里的多张图片 src）。"""
        raise NotImplementedError

    def self_text(self) -> str:
        raise NotImplementedError

    def self_attr(self, name: str) -> str:
        raise NotImplementedError

    def html(self) -> str:
        raise NotImplementedError


class PlaywrightDom(Dom):
    """包装 Playwright 的 Page 或 Locator（两者 API 兼容部分一致）。"""

    def __init__(self, root, timeout: int = 2500):
        self._root = root
        self._t = timeout
        self._is_page = hasattr(root, "content")

    def count(self, selector: str) -> int:
        try:
            return self._root.locator(selector).count()
        except Exception:
            return 0

    def text(self, selector: str) -> str:
        try:
            loc = self._root.locator(selector).first
            if not loc.count():
                return ""
            return (loc.inner_text(timeout=self._t) or "").strip()
        except Exception:
            return ""

    def texts(self, selector: str) -> list[str]:
        out: list[str] = []
        try:
            loc = self._root.locator(selector)
            for i in range(min(loc.count(), 300)):
                try:
                    out.append((loc.nth(i).inner_text(timeout=self._t) or "").strip())
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def attr(self, selector: str, name: str) -> str:
        try:
            loc = self._root.locator(selector).first
            if not loc.count():
                return ""
            return (loc.get_attribute(name, timeout=self._t) or "").strip()
        except Exception:
            return ""

    def sub(self, selector: str, limit: int = 200) -> list[Dom]:
        out: list[Dom] = []
        try:
            loc = self._root.locator(selector)
            for i in range(min(loc.count(), limit)):
                out.append(PlaywrightDom(loc.nth(i), self._t))
        except Exception:
            pass
        return out

    def attr_list(self, selector: str, name: str, limit: int = 60) -> list[str]:
        out: list[str] = []
        try:
            loc = self._root.locator(selector)
            for i in range(min(loc.count(), limit)):
                try:
                    v = loc.nth(i).get_attribute(name, timeout=self._t)
                    if v:
                        out.append(v.strip())
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def self_text(self) -> str:
        try:
            if self._is_page:
                return (self._root.locator("body").inner_text(timeout=self._t) or "").strip()
            return (self._root.inner_text(timeout=self._t) or "").strip()
        except Exception:
            return ""

    def self_attr(self, name: str) -> str:
        try:
            if self._is_page:
                return ""
            return (self._root.get_attribute(name, timeout=self._t) or "").strip()
        except Exception:
            return ""

    def html(self) -> str:
        try:
            if self._is_page:
                return self._root.content()
            return self._root.inner_html(timeout=self._t)
        except Exception:
            return ""

    def eval_js(self, script: str):
        """在页面上下文执行 JS 并返回结果（仅浏览器路径支持）。

        某些站点的 SPEC 结构（如 REGZA 的分组标题+多小表）用 CSS 选择器难以
        表达 heading 与 table 的关联，适配器可用此方法一次性抽出结构化数据。
        """
        try:
            return self._root.evaluate(script)
        except Exception:
            return None


class LxmlDom(Dom):
    """静态 HTML 的 CSS 抽取（可选依赖 lxml + cssselect）。"""

    def __init__(self, root):
        self._root = root

    @staticmethod
    def parse(html_text: str) -> "LxmlDom | None":
        if not _HAS_LXML or not html_text:
            return None
        try:
            return LxmlDom(_lxml_html.fromstring(html_text))
        except Exception:
            return None

    def _find(self, selector: str) -> list:
        try:
            return self._root.cssselect(selector)
        except Exception:
            return []

    def count(self, selector: str) -> int:
        return len(self._find(selector))

    def text(self, selector: str) -> str:
        els = self._find(selector)
        return (els[0].text_content() or "").strip() if els else ""

    def texts(self, selector: str) -> list[str]:
        return [(e.text_content() or "").strip() for e in self._find(selector)]

    def attr(self, selector: str, name: str) -> str:
        els = self._find(selector)
        return (els[0].get(name) or "").strip() if els else ""

    def sub(self, selector: str, limit: int = 200) -> list[Dom]:
        return [LxmlDom(e) for e in self._find(selector)[:limit]]

    def attr_list(self, selector: str, name: str, limit: int = 60) -> list[str]:
        out = []
        for e in self._find(selector)[:limit]:
            v = e.get(name)
            if v:
                out.append(v.strip())
        return out

    def self_text(self) -> str:
        return (self._root.text_content() or "").strip()

    def self_attr(self, name: str) -> str:
        return (self._root.get(name) or "").strip()

    def html(self) -> str:
        try:
            return _lxml_html.tostring(self._root, encoding="unicode")
        except Exception:
            return ""


# ---------------------------------------------------------------- Fetcher


class BaseFetcher:
    def __init__(self, rate_limiter: RateLimiter | None = None,
                 detector: BlockDetector | None = None,
                 cache: DiskCache | None = None):
        self.rate = rate_limiter or RateLimiter()
        self.detector = detector or BlockDetector()
        self.cache = cache if cache is not None else DiskCache()

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class HttpFetcher(BaseFetcher):
    """纯 HTTP 抓取（标准库 urllib）。适合 L1 温和站点与 JSON 接口。"""

    def __init__(self, proxy: str | None = None, **kw):
        super().__init__(**kw)
        self.proxy = resolve_proxy(proxy or "")
        handlers = [urllib.request.ProxyHandler(
            {"http": self.proxy, "https": self.proxy} if self.proxy else {})]
        self._opener = urllib.request.build_opener(*handlers)

    def get(self, url: str, headers: dict | None = None,
            use_cache: bool = True) -> FetchResult:
        if use_cache:
            hit = self.cache.get(url)
            if hit:
                return FetchResult(url=url, status=hit.get("status", 200),
                                   html=hit.get("html", ""), title=hit.get("title", ""),
                                   from_cache=True)
        h = {"User-Agent": config.USER_AGENT,
             "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
             "Accept-Language": "en-CA,en;q=0.9",
             "Accept-Encoding": "gzip"}
        if headers:
            h.update(headers)

        last_err = ""
        retry_limit = max(0, config.MAX_RETRIES_PER_REQUEST)
        for attempt in range(retry_limit + 1):
            self.rate.wait(url)
            t0 = time.time()
            try:
                req = urllib.request.Request(url, headers=h, method="GET")
                with self._opener.open(req, timeout=config.HTTP_TIMEOUT) as resp:
                    raw = resp.read()
                    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                        try:
                            raw = gzip.decompress(raw)
                        except OSError:
                            pass
                    text = raw.decode("utf-8", "replace")
                    status = resp.status
            except urllib.error.HTTPError as e:
                status = e.code
                try:
                    text = e.read().decode("utf-8", "replace")
                except Exception:
                    text = ""
                last_err = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                self.rate.feedback(url, -1)
                last_err = f"{type(e).__name__}: {e}"
                if attempt < retry_limit:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return FetchResult(url=url, status=-1, error=last_err,
                                   elapsed=time.time() - t0)

            self.rate.feedback(url, status)
            blocked, reason = self.detector.check(status, text)
            res = FetchResult(url=url, status=status, html=text, blocked=blocked,
                              block_reason=reason, elapsed=time.time() - t0,
                              error="" if 200 <= status < 300 else last_err)
            # 404/其它明确 4xx 不重试；网络异常、408、429、5xx 只按有限次数重试。
            if _is_retryable_status(status) and attempt < retry_limit:
                self.rate.feedback(url, -2)
                time.sleep(1.5 * (attempt + 1))
                continue
            if res.ok and use_cache:
                self.cache.set(url, {"status": status, "html": text, "title": ""})
            return res
        return FetchResult(url=url, status=-1, error=last_err or "unknown")

    def dom(self, res: FetchResult) -> Dom | None:
        """静态 HTML 转 DOM（需 lxml；未安装则返回 None，只能走 regex/jsonld）。"""
        return LxmlDom.parse(res.html)


class BrowserFetcher(BaseFetcher):
    """Playwright 抓取。L2/L3 站点必须走这条路（JS 渲染 / 反爬）。

    复用同一个 browser + context，逐个页面开关；使用后必须 close()。

    登录态持久化（storage_state）：
      site_code 传入站点 code 时，自动读写 data/session/<code>.state.json。
      加载：context 创建时注入 cookie/localStorage，使已登录会话生效；
      保存：close() 时回写（且仅在有页面成功访问过时才回写，避免用空白
            会话覆盖掉有效登录态）。
      没有该文件时行为与改动前完全一致（无状态抓取）。
    """

    def __init__(self, proxy: str | None = None, headless: bool | None = None,
                 site_code: str = "", storage_state: str | None = None,
                 persist_state: bool | None = None,
                 profile_dir: str | None = None, **kw):
        super().__init__(**kw)
        self.proxy = resolve_proxy(proxy or "")
        self.headless = config.HEADLESS if headless is None else headless
        self._pw = None
        self._browser = None
        self._ctx = None
        self.channel_used = ""
        # ---- 登录态 ----
        self.site_code = (site_code or "").strip()
        # storage_state: 显式路径 > 按 site_code 推导 > 空（不持久化）
        if storage_state:
            self.state_path = storage_state
        elif self.site_code and config.storage_enabled():
            self.state_path = config.session_path(self.site_code)
        else:
            self.state_path = ""
        # 默认：配了路径就回写；也可显式关闭（只读复用别人的登录态）
        self.persist_state = (bool(self.state_path) if persist_state is None
                              else bool(persist_state))
        self.state_loaded = False
        self.state_saved = False
        self._touched = False        # 是否成功打开过页面（决定 close 时是否回写）
        # 本次会话是否命中过登录墙。命中过就拒绝回写 state：
        # 匿名抓取同样有一堆 cookie，存下来会污染登录态并使 login-status 误报。
        self._saw_login_wall = False
        # ---- 持久化 profile（真实用户目录，反爬更信任）----
        # 显式传入 > 按 site_code 推导（仅当 OVERSEAS_USE_PROFILE=1）> 不使用
        if profile_dir:
            self.profile_dir = profile_dir
        elif self.site_code and os.environ.get("OVERSEAS_USE_PROFILE", "") not in ("", "0", "false", "False"):
            self.profile_dir = config.profile_dir(self.site_code)
        else:
            self.profile_dir = ""
        self._persistent = False

    # ---- 生命周期 ----
    def _ensure(self) -> None:
        if self._ctx is not None:
            return
        try:
            from playwright.sync_api import Error as PWError
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError(
                "需要 Playwright：pip install playwright（Linux 另需设置 "
                "OVERSEAS_BROWSER_EXECUTABLE 指向离线 Chrome）") from e

        self._pw = sync_playwright().start()

        # ---- 优先：持久化 profile（真实用户目录，反爬更信任、登录态更稳）----
        if self.profile_dir:
            try:
                os.makedirs(self.profile_dir, exist_ok=True)
                pkw: dict = {
                    "user_data_dir": self.profile_dir,
                    "headless": self.headless,
                    "args": list(config.BROWSER_ARGS),
                    "ignore_https_errors": True,
                    "locale": config.LOCALE,
                    "viewport": {"width": 1440, "height": 900},
                }
                if self.proxy:
                    pkw["proxy"] = {"server": self.proxy}
                if config.BROWSER_EXECUTABLE:
                    pkw["executable_path"] = config.BROWSER_EXECUTABLE
                else:
                    # Edge/Chrome 真实通道；用 channel 让 UA 与真实浏览器一致
                    for ch in config.BROWSER_CHANNELS:
                        if ch and ch != "chromium":
                            pkw["channel"] = ch
                            break
                self._ctx = self._pw.chromium.launch_persistent_context(**pkw)
                self._browser = None
                self._persistent = True
                self.channel_used = "persistent:" + str(pkw.get("channel") or "chromium")
                self._ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
                self._ctx.add_init_script(_STEALTH_JS)
                self.state_loaded = True      # profile 自带登录态
                return
            except Exception as e:
                # profile 打不开（被其它进程占用等）→ 回退到临时 context
                last = e
                try:
                    if self._ctx is not None:
                        self._ctx.close()
                except Exception:
                    pass
                self._ctx = None
                print(f"[WARN] 持久化 profile 打开失败，回退临时上下文："
                      f"{type(e).__name__}: {str(e)[:100]}")

        channels = [None] if config.BROWSER_EXECUTABLE else config.BROWSER_CHANNELS
        last = None
        for ch in channels:
            kw: dict = {"headless": self.headless, "args": list(config.BROWSER_ARGS)}
            if self.proxy:
                kw["proxy"] = {"server": self.proxy}
            if config.BROWSER_EXECUTABLE:
                kw["executable_path"] = config.BROWSER_EXECUTABLE
            elif ch != "chromium":
                kw["channel"] = ch
            try:
                self._browser = self._pw.chromium.launch(**kw)
                self.channel_used = "offline" if config.BROWSER_EXECUTABLE else str(ch)
                break
            except PWError as e:
                last = e
                continue
        if self._browser is None:
            self._pw.stop()
            self._pw = None
            raise RuntimeError(
                f"无法启动浏览器（试过 "
                f"{config.BROWSER_EXECUTABLE or config.BROWSER_CHANNELS}）：{last}")
        ctx_kw: dict = {
            "user_agent": config.USER_AGENT, "locale": config.LOCALE,
            "ignore_https_errors": True, "viewport": {"width": 1440, "height": 900},
        }
        # 注入已保存的登录态。文件缺失/损坏时静默降级为无状态抓取，
        # 不让一个坏 state.json 阻断整条抓取链。
        if self.state_path and os.path.exists(self.state_path):
            try:
                ctx_kw["storage_state"] = self.state_path
                self.state_loaded = True
            except Exception:
                self.state_loaded = False
        self._ctx = self._browser.new_context(**ctx_kw)
        self._ctx.set_default_navigation_timeout(config.NAV_TIMEOUT_MS)
        # 增强 stealth：隐藏自动化特征 + 模拟真实浏览器指纹
        self._ctx.add_init_script(_STEALTH_JS)

    def wait_for_login(self, url: str, timeout: int = 300,
                       interval: int = 5, verbose: bool = True,
                       probe_url: str = "", verify_every: int = 15,
                       cookie_url: str = "") -> bool:
        """打开 url，被动等待人工登录完成。

        ★ 三条硬规则（来自三次踩坑）：
          1) 登录全过程**只打开一次页面**：不新开标签页（新标签会抢前台，
             让用户正在登录的页面"消失"＝闪退）。
          2) **绝不导航到需要登录的深层页**（如 /gp/css/order-history）：
             未登录时它必然 302 到 /ap/signin，用户会直接看到登录墙。
          3) 判定顺序：先看 cookie，再用**商品页/首页页眉**是否出现
             "Hello, <名字>" 做二次确认（reload 同一页，不换页）。

        为什么不能只靠 at-main：该 cookie 仅在用户勾选「Keep me signed in」
        时才下发。只认它会漏判真实登录（实测踩过）。所以采用分级判据：
          at-main/x-main 存在        → 直接判定已登录
          session-token 存在 + 页眉显示 Hello, X → 判定已登录
        """
        import time as _t

        self._ensure()
        deadline = _t.time() + max(10, int(timeout))

        self.rate.wait(url)
        pg = self._ctx.new_page()
        # 被动导航信号：用户登录时 Amazon 自己会跳转/重渲染，我们只观察，
        # **绝不主动 reload**（reload 会刷掉用户正在填写的登录表单——实测被投诉过）。
        nav_signal = [0.0]

        def _on_nav(*_a, **_k):
            nav_signal[0] = _t.time()

        for ev in ("domcontentloaded", "load", "framenavigated"):
            try:
                pg.on(ev, _on_nav)
            except Exception:
                pass
        try:
            pg.goto(url, wait_until="domcontentloaded",
                    timeout=min(config.NAV_TIMEOUT_MS, 45000))
        except Exception as e:
            if verbose:
                print(f"    [登录] 打开页面异常（可继续手动操作）："
                      f"{type(e).__name__}: {str(e)[:80]}")
        self._touched = True

        # 读 cookie 的域：不同 Amazon 站点域不同（.ca / .com / .com.mx），
        # 硬编码会导致美/墨站读不到凭证。默认取当前 URL 的 origin。
        ck_url = cookie_url or ""
        if not ck_url:
            try:
                p = urllib.parse.urlsplit(url)
                ck_url = f"{p.scheme}://{p.netloc}/"
            except Exception:
                ck_url = url

        def _cookie_map() -> dict:
            try:
                return {str(c.get("name") or ""): str(c.get("value") or "")
                        for c in self._ctx.cookies(ck_url)}
            except Exception:
                return {}

        def _page_says_logged_in() -> bool:
            """**只读**当前页面 DOM（不导航、不 reload），判断是否已登录。

            硬判据：页眉出现 Sign Out。
            注意 /gp/css/order-history 链接在登出状态的账号菜单里也有，不能用作判据；
            也不能用 'Hello, *'，因为登出文案就是 'Hello, sign in'（都实测踩过）。
            """
            try:
                html = pg.content()
            except Exception:
                return False
            if "/ap/signin" in (pg.url or ""):
                return False
            low = html.lower()
            if "sign out" not in low and "signout" not in low and ">Sign Out<" not in html:
                return False
            m = re.search(
                r'id="nav-link-accountList-nav-line-1"[^>]*>([^<]{0,60})', html)
            label = (m.group(1).strip() if m else "")
            if label.lower() in ("hello, sign in", "sign in",
                                 "hello, sign in account & lists"):
                return False
            return True

        if verbose:
            print("    [登录] 页面已就绪。")
            print("    [登录] ★ 完全被动：不刷新、不新开标签页、不跳转，请放心操作。")
            print("    [登录] 成功标志：右上角显示 Hello, <你的名字>。")

        attempt = 0
        while _t.time() < deadline:
            attempt += 1
            cmap = _cookie_map()
            # 判据 A：持久登录凭证。
            # ★ Amazon 的鉴权 cookie 带**国别后缀**：
            #   .ca → at-acbca / sess-at-acbca / x-acbca
            #   .com → at-main / sess-at-main / x-main
            #   .com.mx → at-acbmx（同类模式）
            # 只认 at-main 会把 ca/mx 误判为未登录（实测连错 3 次）。
            # 这里用前缀匹配覆盖所有国别变体。
            for name in cmap:
                if name.startswith(("at-", "sess-at-", "x-")) and not \
                        name.startswith(("at-a-glance", "x-amz")):
                    if verbose:
                        print(f"    [登录] 检测到凭证 cookie {name} → 登录成功。")
                    return True
            # 判据 B：页面**已离开登录页**且静置数秒 → 只读 DOM 看 Sign Out。
            # 不依赖 session-id 是否变化（登录不一定会换 session-id，会漏判）。
            # 仍在 /ap/signin 上时一律不动：那是登录流程的中间步骤，
            # 而且此时读 DOM 也无意义。
            try:
                cur_url = pg.url or ""
            except Exception:
                cur_url = ""
            settled = (_t.time() - nav_signal[0]) > 5 if nav_signal[0] else False
            if ("/ap/signin" not in cur_url) and cur_url.startswith("http") and settled:
                if _page_says_logged_in():
                    if verbose:
                        print("    [登录] 页面已离开登录页且出现 Sign Out "
                              "→ 登录成功。")
                    return True
            if verbose and attempt % 12 == 1:
                left = int(deadline - _t.time())
                where = "/ap/signin" if "/ap/signin" in cur_url else (cur_url[:45] or "?")
                print(f"    [登录] 等待中…（剩 {left}s；当前在 {where}）")
            _t.sleep(max(2, int(interval)))
        if verbose:
            print("    [登录] 超时，未确认登录成功。")
        return False

    def _verify_logged_in(self, url: str, verbose: bool = True,
                          probe_url: str = "") -> bool:
        """确认会话是否**真的**登录了。

        只判断"不在登录墙"是不够的——Amazon 的商品页匿名也能打开，会假阳。
        因此优先探一个**必须登录**的页面（账号订单历史），看是否被跳登录墙；
        没有 probe_url 时退回原判据。

        probe_url 可用 adapter.login_probe_url 指定；Amazon 默认用
        /gp/css/order-history（实测未登录必跳 /ap/signin）。
        """
        from .infra import login_wall_hit
        target = probe_url or url
        vp = None
        try:
            self.rate.wait(target)
            vp = self._ctx.new_page()
            vp.goto(target, wait_until="domcontentloaded",
                    timeout=min(config.NAV_TIMEOUT_MS, 45000))
            vp.wait_for_timeout(1800)
            fin, title = vp.url or "", (vp.title() or "").strip()
            if login_wall_hit(fin, title, ""):
                if verbose:
                    print(f"    [登录] 校验：需登录页仍跳登录墙 "
                          f"({title[:40]!r}) → 未登录")
                return False
            if probe_url and probe_url != url:
                if verbose:
                    print(f"    [登录] 校验：需登录页可访问 "
                          f"({title[:40]!r}) → 已登录 ✓")
                return True
            if verbose:
                print(f"    [登录] 确认：{title[:50]!r} @ {fin[:60]} → 已登录")
            return True
        except Exception as e:
            if verbose:
                print(f"    [登录] 确认失败：{type(e).__name__}")
            return False
        finally:
            try:
                if vp is not None:
                    vp.close()
            except Exception:
                pass

    def save_state(self, force: bool = False) -> bool:
        """把当前 context 的登录态写入 state_path。失败不抛，返回是否成功。

        只在本次确实打开过页面（_touched）时才写：否则一个空 context 会把
        磁盘上有效的登录态覆盖掉。

        force=False（默认）时还会拒绝在"登录墙"上回写：匿名抓取同样会拿到一堆
        cookie（session-id/ubid 等），把它当登录态存下来会污染会话文件、并让
        login-status 误报"已登录"。只有真的登录成功（或显式 force）才落盘。
        """
        if not (self.state_path and self.persist_state and self._ctx is not None):
            return False
        if not self._touched:
            return False
        if self._saw_login_wall and not force:
            return False
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            self._ctx.storage_state(path=self.state_path)
            self.state_saved = True
            return True
        except Exception:
            return False

    def _close_pages(self) -> None:
        """关闭本 context 下所有页面。

        持久化 profile 下必须逐页关闭——直接 close 整个 context 容易触发
        Chromium 崩溃（"Target page, context or browser has been closed"）。
        """
        try:
            pages = list(getattr(self._ctx, "pages", []) or [])
        except Exception:
            pages = []
        for p in pages:
            try:
                p.close()
            except Exception:
                pass

    def close(self, save_state: bool = True) -> None:
        if save_state:
            self.save_state()
        if self._persistent:
            # 持久化 profile：先关页面再关 context，避免 profile 写坏
            self._close_pages()
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:
                pass
        else:
            for obj, meth in ((self._ctx, "close"), (self._browser, "close")):
                try:
                    if obj is not None:
                        getattr(obj, meth)()
                except Exception:
                    pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._browser = self._pw = None

    # ---- 抓取 ----
    @contextmanager
    def page(self, url: str, wait_selector: str = "",
             wait_until: str = "domcontentloaded", extra_wait_ms: int = 0,
             settle_ms: int | None = None, scroll_passes: int | None = None,
             scroll_wait_ms: int | None = None,
             nav_timeout_ms: int | None = None,
             click_texts: list[str] | None = None,
             click_selectors: list[str] | None = None,
             click_repeats: int | None = None,
             click_wait_ms: int | None = None,
             click_growth_selector: str = "",
             scroll_until_stable: bool = False,
             scroll_growth_selector: str = "",
             scroll_max_passes: int | None = None,
             scroll_stable_rounds: int = 2,
             capture_response_pattern: str = "",
             capture_response_limit: int = 0,
              human_like: bool = False,
             ) -> Iterator[tuple[FetchResult, Dom | None]]:
        """打开页面并产出 (FetchResult, Dom)。瞬时错误有限重试，404 不重试。

        可选逐次覆盖等待参数（settle/scroll/nav_timeout），None 时沿用 config 默认，
        用于按页面类型削减固定等待（7.4）。不传这些参数时行为与原来完全一致。
        """
        # 逐次参数回落到全局默认，保证不传参时行为完全不变。
        eff_settle = config.SETTLE_MS if settle_ms is None else max(0, int(settle_ms))
        eff_scroll_passes = (config.SCROLL_PASSES if scroll_passes is None
                             else max(0, int(scroll_passes)))
        eff_scroll_wait = (config.SCROLL_WAIT_MS if scroll_wait_ms is None
                           else max(0, int(scroll_wait_ms)))
        eff_nav_timeout = (config.NAV_TIMEOUT_MS if nav_timeout_ms is None
                           else max(1, int(nav_timeout_ms)))
        eff_click_repeats = (1 if click_repeats is None
                             else max(0, int(click_repeats)))
        eff_click_wait = (3500 if click_wait_ms is None
                          else max(0, int(click_wait_ms)))

        self._ensure()
        retry_limit = max(0, config.MAX_RETRIES_PER_REQUEST)
        for attempt in range(retry_limit + 1):
            self.rate.wait(url)
            t0 = time.time()
            pg = None
            res = FetchResult(url=url)
            dom: Dom | None = None
            try:
                pg = self._ctx.new_page()
                captured_responses = []
                if capture_response_pattern:
                    def _capture_response(response):
                        try:
                            if re.search(capture_response_pattern, response.url, re.I):
                                captured_responses.append(response)
                        except Exception:
                            pass
                    pg.on("response", _capture_response)
                r = pg.goto(url, wait_until=wait_until, timeout=eff_nav_timeout)
                # ★ human_like: 模拟鼠标移动 + 随机延迟（反检测增强）
                if human_like:
                    import random
                    try:
                        pg.wait_for_timeout(random.randint(800, 2000))
                        pg.mouse.move(random.randint(100, 800), random.randint(100, 600))
                        pg.wait_for_timeout(random.randint(200, 500))
                        pg.mouse.move(random.randint(200, 1200), random.randint(200, 800))
                        pg.wait_for_timeout(random.randint(300, 800))
                    except Exception:
                        pass
                res.status = r.status if r else 0
                self._touched = True
                # 最终 URL：跳转（尤其跳登录页）是判断会话失效的关键信号
                try:
                    res.meta["final_url"] = pg.url or ""
                except Exception:
                    res.meta["final_url"] = ""
                try:
                    res.title = (pg.title() or "").strip()
                except Exception:
                    res.title = ""

                # 明确的非 2xx 页面无需再等待选择器/networkidle，避免 404 型号页
                # 每次额外消耗多个等待周期。
                if 200 <= res.status < 300:
                    if wait_selector:
                        try:
                            pg.wait_for_selector(wait_selector, timeout=8000)
                        except Exception:
                            pass
                    if eff_settle > 0:
                        try:
                            pg.wait_for_load_state("networkidle", timeout=eff_settle)
                        except Exception:
                            pass
                    if eff_scroll_passes > 0:
                        try:
                            if scroll_until_stable:
                                # 入口页懒加载不能依赖固定前三次滚动；持续滚动到
                                # 目标链接数量稳定，最大次数只作为防止站点脚本失控的安全阀。
                                max_passes = (max(1, int(scroll_max_passes))
                                              if scroll_max_passes is not None
                                              else max(20, eff_scroll_passes))
                                stable_rounds = max(1, int(scroll_stable_rounds or 1))
                                previous = (pg.locator(scroll_growth_selector).count()
                                            if scroll_growth_selector else -1)
                                stable = 0
                                for index in range(max_passes):
                                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    if human_like:
                                        try:
                                            import random as _r
                                            pg.mouse.move(_r.randint(100,1200), _r.randint(100,800))
                                            pg.wait_for_timeout(_r.randint(200,600))
                                        except Exception:
                                            pass
                                    pg.wait_for_timeout(eff_scroll_wait)
                                    current = (pg.locator(scroll_growth_selector).count()
                                               if scroll_growth_selector else -1)
                                    if scroll_growth_selector and current <= previous:
                                        stable += 1
                                    else:
                                        stable = 0
                                    previous = current
                                    if scroll_growth_selector and stable >= stable_rounds:
                                        res.meta.update({
                                            "scroll_passes": index + 1,
                                            "scroll_termination": "stable",
                                            "scroll_link_count": current,
                                        })
                                        break
                                else:
                                    res.meta.update({
                                        "scroll_passes": max_passes,
                                        "scroll_termination": "safety_limit",
                                        "scroll_link_count": previous,
                                    })
                            else:
                                for _ in range(eff_scroll_passes):
                                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    if human_like:
                                        try:
                                            import random as _r
                                            pg.mouse.move(_r.randint(100,1200), _r.randint(100,800))
                                            pg.wait_for_timeout(_r.randint(200,600))
                                        except Exception:
                                            pass
                                    pg.wait_for_timeout(eff_scroll_wait)
                                res.meta.update({
                                    "scroll_passes": eff_scroll_passes,
                                    "scroll_termination": "fixed_passes",
                                })
                            pg.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            res.meta.setdefault("scroll_termination", "error")
                    # 可选：点击展开按钮（如型号页 Hisense「Full Specs」），
                    # 点开后规格才渲染。
                    if click_texts:
                        for _txt in click_texts:
                            try:
                                loc = pg.get_by_text(_txt, exact=False).first
                                if loc.count():
                                    loc.scroll_into_view_if_needed(timeout=4000)
                                    loc.click(timeout=4000)
                                    pg.wait_for_timeout(eff_click_wait)
                            except Exception:
                                pass
                        # 点击后再滚动；入口仍以目标链接集合稳定作为终止条件。
                        try:
                            if scroll_until_stable and scroll_growth_selector:
                                previous = pg.locator(scroll_growth_selector).count()
                                stable = 0
                                max_passes = (max(1, int(scroll_max_passes))
                                              if scroll_max_passes is not None
                                              else max(20, eff_scroll_passes))
                                for index in range(max_passes):
                                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    if human_like:
                                        try:
                                            import random as _r
                                            pg.mouse.move(_r.randint(100,1200), _r.randint(100,800))
                                            pg.wait_for_timeout(_r.randint(200,600))
                                        except Exception:
                                            pass
                                    pg.wait_for_timeout(eff_scroll_wait)
                                    current = pg.locator(scroll_growth_selector).count()
                                    stable = stable + 1 if current <= previous else 0
                                    previous = current
                                    if stable >= max(1, int(scroll_stable_rounds or 1)):
                                        res.meta.update({
                                            "post_click_scroll_passes": index + 1,
                                            "post_click_scroll_termination": "stable",
                                            "scroll_link_count": current,
                                        })
                                        break
                                else:
                                    res.meta.update({
                                        "post_click_scroll_passes": max_passes,
                                        "post_click_scroll_termination": "safety_limit",
                                        "scroll_link_count": previous,
                                    })
                            else:
                                for _ in range(max(1, eff_scroll_passes)):
                                    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                                    if human_like:
                                        try:
                                            import random as _r
                                            pg.mouse.move(_r.randint(100,1200), _r.randint(100,800))
                                            pg.wait_for_timeout(_r.randint(200,600))
                                        except Exception:
                                            pass
                                    pg.wait_for_timeout(eff_scroll_wait)
                            pg.evaluate("window.scrollTo(0, 0)")
                        except Exception:
                            res.meta.setdefault("post_click_scroll_termination", "error")
                    # 可选：按 CSS 选择器重复点击入口按钮（如 Hisense Load More）。
                    # 如果提供增长选择器，按钮点击后没有新增目标元素时立即停止。
                    if click_selectors and eff_click_repeats > 0:
                        for selector in click_selectors:
                            for _ in range(eff_click_repeats):
                                try:
                                    loc = pg.locator(selector).last
                                    if not loc.count() or not loc.is_visible():
                                        break
                                    if loc.is_disabled() or (loc.get_attribute(
                                            "aria-disabled") or "").lower() == "true":
                                        break
                                    before = (pg.locator(click_growth_selector).count()
                                              if click_growth_selector else -1)
                                    loc.scroll_into_view_if_needed(timeout=4000)
                                    try:
                                        loc.click(timeout=5000)
                                    except Exception:
                                        # 同意层等非关键遮挡不应阻止入口按钮尝试。
                                        loc.click(timeout=5000, force=True)
                                    if eff_click_wait > 0:
                                        pg.wait_for_timeout(eff_click_wait)
                                    if click_growth_selector:
                                        after = pg.locator(click_growth_selector).count()
                                        if after <= before:
                                            break
                                except Exception:
                                    res.meta.setdefault("click_termination", "error")
                                    break
                            res.meta.setdefault("click_termination", "stable_or_limit")
                    if extra_wait_ms > 0:
                        try:
                            pg.wait_for_timeout(extra_wait_ms)
                        except Exception:
                            pass
                    if capture_response_pattern and captured_responses:
                        seen_bodies: set[str] = set()
                        body_limit = max(0, int(capture_response_limit or 0))
                        for response in captured_responses:
                            try:
                                body = response.text() or ""
                            except Exception:
                                continue
                            if body_limit:
                                body = body[:body_limit]
                            if body and body not in seen_bodies:
                                seen_bodies.add(body)
                                res.response_bodies.append(body)
                        res.meta["captured_response_count"] = len(res.response_bodies)

                dom = PlaywrightDom(pg)
                anchor = None
                if wait_selector:
                    anchor = dom.count(wait_selector) > 0
                head = ""
                try:
                    head = pg.content()[:8000]
                except Exception:
                    pass
                res.blocked, res.block_reason = self.detector.check(
                    res.status, head, res.title, anchor_present=anchor,
                    final_url=str(res.meta.get("final_url") or ""))
                if res.block_reason == "not_logged_in":
                    self._saw_login_wall = True
            except Exception as e:
                res.status = res.status or -1
                res.error = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
                dom = None

            res.elapsed = time.time() - t0
            transient = res.status <= 0 or _is_retryable_status(res.status)
            if transient and attempt < retry_limit:
                self.rate.feedback(url, -2 if res.status > 0 else -1)
                try:
                    if pg is not None:
                        pg.close()
                except Exception:
                    pass
                time.sleep(1.5 * (attempt + 1))
                continue

            self.rate.feedback(url, -2 if res.blocked else res.status)
            try:
                yield res, dom
            finally:
                try:
                    if pg is not None:
                        pg.close()
                except Exception:
                    pass
            return

    def get_html(self, url: str, wait_selector: str = "") -> FetchResult:
        """只要 HTML 文本（不需要 DOM 操作时用）。"""
        with self.page(url, wait_selector=wait_selector) as (res, dom):
            if dom is not None:
                res.html = dom.html()
            return res
