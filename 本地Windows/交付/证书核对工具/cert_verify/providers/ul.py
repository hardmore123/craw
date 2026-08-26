r"""UL Prospector（iq.ulprospector.com）—— 需登录 + Cloudflare 反爬，用 Playwright 登录态

## 抓取路径（已实测打通，证书号 E502081）

1. 自动补全解析文件号 → 拿到内部查询码 qm：
   GET /en/suggest/_all?q=<文件号>
   返回 JSON，File Number 组（key=10006）给出 "qm":"10006:<内部id>"
   例：E502081 -> qm=10006:123401（count=2）

2. 用 qm 精确过滤，拿结果表：
   GET /en/search/_results?qm=10006:123401
   返回 HTML 表格，每行一个文档：
     Document Name（如 FOWX2.E502081，= CCN.文件号）
     Company Name （制造商/证书持有人）
     UL CCN Description（产品类别）
     链接 /en/document?e=<内部id>（详情页，含更细的型号/标准）

## 字段映射

| UL 字段 | 映射到 |
|---|---|
| Company Name | 制造商 |
| Document Name 里的文件号 / CCN | 证书号 / 型号(粗) |
| UL CCN Description | 产品类别 |
| 结果行存在（列在库中） | 证书状态=Listed（有效）|

注意：UL 结果列表**不直接给标准和精确型号**（要进 /en/document 详情页）。
所以标准、精确型号返回空，比对时记「网站无此项」，不判不一致。
需要的话可扩展 _fetch_detail 进详情页取。

## 依赖

- Playwright + 本机 Edge（channel=msedge，无需下载 chromium）
- 首次登录：python -m cert_verify.ul_login
- Cloudflare 只有真浏览器能过，所以每次查询都起一个（复用登录态配置）。
  批量查询时同一个 provider 实例复用同一浏览器会话（见 UlSession）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse

import random

from ..http_client import QUERY_JITTER, QUERY_MIN_INTERVAL, resolve_proxy
from ..models import BLOCKED, ERROR, FOUND, NOT_FOUND, UNSUPPORTED, CertRecord
from .base import BaseProvider, text_of

PROFILE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           ".ul_profile")
HOME = "https://iq.ulprospector.com/en"
SUGGEST = "https://iq.ulprospector.com/en/suggest/_all?q={q}"
RESULTS = "https://iq.ulprospector.com/en/search/_results?qm={qm}"
DOC = "https://iq.ulprospector.com/en/document?e={e}"

_ROW_RE = re.compile(
    r'<a href="(/en/document\?e=\d+)"[^>]*class="entry-link">\s*<span>(.*?)</span>',
    re.S | re.I)
_COMPANY_RE = re.compile(
    r'data-display-name="Company Name".*?<span>(.*?)</span>', re.S | re.I)
_CCN_RE = re.compile(
    r'data-display-name="UL CCN Description".*?<span>(.*?)</span>', re.S | re.I)


class UlProvider(BaseProvider):
    name = "UL"
    # UL File Number：E502081 / E123456 / MH12345 ...
    number_patterns = (r"^(E|MH|SA|QQ|LR|AU)\s*\d{4,7}$",)

    def __init__(self, headless: bool = True, proxy: str | None = None,
                 interval: float | None = None, jitter: float | None = None):
        self.headless = headless
        # UL 是真浏览器，需要能出网的出口：显式 proxy > UL_PROXY > 系统/VPN 代理。
        # iq.ulprospector.com 直连会被公司网关拦（SSL 拦截），所以默认走系统代理。
        self.proxy = proxy or os.environ.get("UL_PROXY") or resolve_proxy()
        self._session = None          # 复用浏览器会话（批量时）
        self._reuse = False           # True 时首次查询自动开会话并保持，批量共用一个浏览器
        self._last_ts = 0.0           # 上次查询时间戳，用于两条之间的间隔
        # 查询间隔可由上层（网页设置）注入，默认沿用模块常量
        self.interval = QUERY_MIN_INTERVAL if interval is None else max(0.0, interval)
        self.jitter = QUERY_JITTER if jitter is None else max(0.0, jitter)

    def _throttle(self) -> None:
        """每条查询之间至少间隔 self.interval + 0~self.jitter 秒。"""
        wait = self.interval - (time.time() - self._last_ts)
        if wait > 0:
            time.sleep(wait + random.uniform(0, self.jitter))
        self._last_ts = time.time()

    def close(self) -> None:
        """关闭并释放复用的浏览器会话（批量结束时调用）。"""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    # ---------------- 会话（批量复用一个浏览器）----------------
    def __enter__(self):
        self._session = UlSession(headless=self.headless, proxy=self.proxy)
        self._session.open()
        return self

    def __exit__(self, *exc):
        if self._session:
            self._session.close()
            self._session = None
        return False

    def query(self, cert_no: str) -> CertRecord:
        rec = CertRecord(query_no=cert_no, source=self.name)
        if not os.path.isdir(PROFILE_DIR):
            rec.status = UNSUPPORTED
            rec.message = "UL 未登录：先跑 python -m cert_verify.ul_login 登录一次"
            return rec

        self._throttle()          # 两条之间 15s + 0~5s 随机间隔

        # 复用模式：首次查询开一个会话并保持，后续行共用同一个浏览器（批量必须这样，
        # 否则每行都重启一次 Edge，极慢还会不停弹/关窗口）。
        if self._reuse and self._session is None:
            self._session = UlSession(headless=self.headless, proxy=self.proxy)
            self._session.open()

        own = self._session is None
        sess = self._session or UlSession(headless=self.headless, proxy=self.proxy)
        try:
            if own:
                sess.open()
            return self._query_with(sess, cert_no, rec)
        except _UlPlaywrightMissing as e:
            rec.status, rec.message = ERROR, str(e)
            return rec
        except Exception as e:  # noqa: BLE001
            rec.status, rec.message = ERROR, f"{type(e).__name__}: {e}"
            return rec
        finally:
            if own and sess:
                sess.close()

    def _query_with(self, sess: "UlSession", cert_no: str, rec: CertRecord) -> CertRecord:
        no = UlSession._norm_no(cert_no)      # 去掉 'UL ' 前缀，只留 EXXXXX
        rows = []

        # 1) 主路径：keyword 自动补全 → File Number 精确码(10006:...) → 结果表。
        #    这等价于「在 keyword 框里输入证书号并选中该 File Number」，已实测可用。
        qm, _sug = sess.suggest_qm(cert_no)
        if qm:
            html = sess.get(RESULTS.format(qm=urllib.parse.quote(qm, safe="")))
            if sess.blocked(html):
                rec.status, rec.message = BLOCKED, "被 Cloudflare 拦截（登录态可能失效，重登一次）"
                return rec
            rows = self._parse_rows(html)

        # 2) 回退：直接用 keyword 查询码 qm=q:<证书号>
        if not rows:
            html = sess.get(RESULTS.format(qm=urllib.parse.quote(f"q:{no}", safe=":")))
            if sess.blocked(html):
                rec.status, rec.message = BLOCKED, "被 Cloudflare 拦截（登录态可能失效，重登一次）"
                return rec
            rows = self._parse_rows(html)

        if not rows:
            rec.status = NOT_FOUND
            rec.message = "UL 未查到该证书号（keyword 自动补全与直查均无结果）"
            return rec

        # 取第一行做主记录，其余进 raw
        first = rows[0]
        docs = "; ".join(dict.fromkeys(r["doc_name"] for r in rows))   # CCN.文件号 列表
        rec.manufacturer = first["company"]
        rec.cert_no_on_site = UlSession._norm_no(cert_no)
        # 注意：UL 列表页只给文档名(CCN.文件号)，不含真实型号/标准 —— 那些在 /en/document 详情页。
        # 所以 model / standard 留空（比对记"网站无此项"），不用 CCN 名硬比造成假不一致。
        rec.model = ""
        rec.standard = ""
        rec.cert_type = "; ".join(dict.fromkeys(r["ccn_desc"] for r in rows if r["ccn_desc"]))
        rec.cert_status = "Listed"      # 列在库中即视为有效
        rec.source_url = HOME + f"/search?qm=q:{UlSession._norm_no(cert_no)}"
        rec.raw_fields = {
            "Company Name": first["company"],
            "Documents(CCN.FileNo)": docs,
            "UL CCN Description": rec.cert_type,
            "__doc_links__": "; ".join("https://iq.ulprospector.com" + r["href"] for r in rows),
            "__count__": str(len(rows)),
        }
        rec.status = FOUND
        return rec

    @staticmethod
    def _parse_rows(html: str) -> list[dict]:
        # 按 <tr> 切，逐行取三列
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I):
            m = _ROW_RE.search(tr)
            if not m:
                continue
            href, doc_name = m.group(1), text_of(m.group(2))
            company = text_of(_COMPANY_RE.search(tr).group(1)) if _COMPANY_RE.search(tr) else ""
            ccn = text_of(_CCN_RE.search(tr).group(1)) if _CCN_RE.search(tr) else ""
            rows.append({"href": href, "doc_name": doc_name,
                         "company": company, "ccn_desc": ccn})
        return rows


# ---------------- Playwright 会话封装 ----------------

class _UlPlaywrightMissing(RuntimeError):
    pass


class UlSession:
    """一个已登录的 UL 浏览器会话；suggest / get 复用它。"""

    def __init__(self, headless: bool = True, proxy: str | None = None):
        self.headless = headless
        self.proxy = proxy
        self._p = None
        self._ctx = None
        self._page = None

    def open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise _UlPlaywrightMissing("需要 Playwright：pip install playwright") from e
        from ..ul_login import _launch_persistent
        self._p = sync_playwright().start()
        self._ctx, _ch = _launch_persistent(self._p, self.proxy, headless=self.headless)
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        try:
            self._page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
            time.sleep(1.5)
        except Exception:
            pass

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
            if self._p:
                self._p.stop()
        except Exception:
            pass
        self._p = self._ctx = self._page = None

    def get(self, url: str) -> str:
        # 这里只等页面加载稳定（非礼貌间隔）；两条查询之间的 15s+抖动在 UlProvider._throttle
        self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(1.2)
        return self._page.content()

    @staticmethod
    def _norm_no(q: str) -> str:
        """去掉机构前缀：表格 I 列写作 'UL E67006' → 'E67006'。"""
        return re.sub(r"^(?:UL|CB|VDE|TUV|T[ÜU]V)\s*[:：]?\s*", "", (q or "").strip(), flags=re.I).strip()

    def suggest_qm(self, q: str):
        q = self._norm_no(q)
        html = self.get(SUGGEST.format(q=urllib.parse.quote(q)))
        try:
            txt = self._page.inner_text("pre")
        except Exception:
            txt = re.sub(r"<[^>]+>", "", html)
        try:
            data = json.loads(txt)
        except Exception:
            m = re.search(r"\{.*\}", txt, re.S)
            data = json.loads(m.group(0)) if m else {}
        items = data.get("items", [])
        for it in items:                       # File Number 组优先
            if str(it.get("qm", "")).startswith("10006:"):
                return it["qm"], it
        for it in items:                       # 退回关键词
            if it.get("qm"):
                return it["qm"], it
        return None, data

    @staticmethod
    def blocked(html: str) -> bool:
        head = (html or "")[:3000].lower()
        return "cloudflare" in head and "cf-error" in head
