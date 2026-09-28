"""TCL（日本）TV 官网 SPEC 适配器（多级流程 + JSON 规格端点）。

实测结构（2026-09）：
- TV 总览 https://www.tcl.com/jp/ja/tvs 的菜单里列出各系列 /jp/ja/tvs/<系列>
  （系列码形如 c755 / c855 / x11k / t6d / a400 …），链接文字含产品名。
- 系列页 /jp/ja/tvs/<系列> 是 SPA，规格不在 HTML，页面用叶子文本列出该系列各
  尺寸型号（如 50C755 / 65C755 …）。
- 规格数据来自 AEM JCR JSON 端点（一个型号一份）：
  /content/brandsite/jp/ja/tvs/<系列>/jcr:content/.../specifications1/
    product_spec.<base64>.json
  其中 base64 = /content/brandsite-product/jp/ja/tvs/<系列>/<型号小写>
  JSON 结构 {code,msg,data:[{tab:分组, specItems:[{name,value}]}]}。

一系列多尺寸型号，各自一份 JSON，抓齐后横排合并成一张多机型表。
"""
from __future__ import annotations

import base64
import json
import re

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_tcl_spec_json
from ...fetchers import Dom
from .. import SiteAdapter

# 系列码：字母+数字[+字母]，如 c755 / x11k / a400m / s5400
_SERIES_SEG = re.compile(r"^[a-z]+\d+[a-z]?$")
# 型号：<尺寸数字><系列码大写>，如 65C755 / 98X11L
_MODEL_RE_TMPL = r"\b(\d{2,3}%s)\b"


class TclJpAdapter(SiteAdapter):
    code = "tcl_jp"
    name = "TCL（日本）"
    base_url = "https://www.tcl.com"
    country = "日本"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 3.0
    supports_spec = True
    spec_entry_wait = "a[href*='/jp/ja/tvs/']"
    # 首页有国家选择器overlay，需额外等待让JS完成加载
    spec_entry_extra_wait_ms = 8000
    # 型号 spec 页是 JSON 端点，等 body 即可
    spec_page_wait = "body"

    ENTRY = "https://www.tcl.com/jp/ja/tvs"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    # ---- JSON 端点构造 ----
    def _spec_json_url(self, series: str, model: str) -> str:
        path = f"/content/brandsite-product/jp/ja/tvs/{series}/{model.lower()}"
        b = base64.b64encode(path.encode()).decode()
        return (f"{self.base_url}/content/brandsite/jp/ja/tvs/{series}/jcr:content/"
                f"root/container/container/container/subnav_menus/specifications1/"
                f"product_spec.{b}.json")

    def model_from_url(self, url: str) -> str:
        """从 JSON 端点 URL 的 base64 段反解型号（大写）。"""
        m = re.search(r"product_spec\.([A-Za-z0-9+/=]+)\.json", url or "")
        if not m:
            return url
        try:
            path = base64.b64decode(m.group(1)).decode()
            return path.rstrip("/").split("/")[-1].upper()
        except Exception:
            return url

    # ---- 多级流程 ----
    def series_entries(self, open_dom):
        """总览页取系列 → 各系列页取型号 → 构造型号 JSON 端点 URL。"""
        # 1) 总览页取系列（菜单里带产品名的 /tvs/<系列> 链接）
        series_list: list[str] = []
        with open_dom(self.ENTRY, self.spec_entry_wait) as (res, dom, html):
            if dom is None or not res.ok:
                return []
            hrefs = dom.attr_list("a[href*='/jp/ja/tvs/']", "href", limit=600)
            hrefs += re.findall(r'href="([^"]*/jp/ja/tvs/[^"]*)"', html or "")
            seen: set[str] = set()
            for h in hrefs:
                seg = (h or "").rstrip("/").split("/")[-1].lower()
                if not _SERIES_SEG.match(seg):
                    continue
                if seg in seen:
                    continue
                seen.add(seg)
                series_list.append(seg)

        # 2) 逐系列页取属于该系列的型号
        entries: list[tuple[str, list[str]]] = []
        for series in series_list:
            model_re = re.compile(_MODEL_RE_TMPL % series.upper(), re.I)
            surl = f"{self.base_url}/jp/ja/tvs/{series}"
            models: list[str] = []
            try:
                with open_dom(surl, "body") as (res, dom, html):
                    if dom is None or not res.ok:
                        continue
                    text = dom.self_text() + " " + (html or "")
                    seen_m: set[str] = set()
                    for m in model_re.findall(text):
                        mu = m.upper()
                        size = int(re.match(r"\d+", mu).group())
                        if not (24 <= size <= 120):    # 过滤误匹配的异常尺寸
                            continue
                        if mu not in seen_m:
                            seen_m.add(mu)
                            models.append(mu)
            except Exception:
                continue
            if not models:
                continue
            # 按尺寸降序
            models.sort(key=lambda x: int(re.match(r"\d+", x).group()), reverse=True)
            urls = [self._spec_json_url(series, m) for m in models]
            entries.append((series.upper(), urls))
        return entries

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        # JSON 端点：从 body 文本取 JSON
        raw = dom.self_text() or dom.html()
        if not raw:
            return empty
        # 可能被包在 <pre> 或直接是文本；截取首个 { 到末个 }
        s = raw.strip()
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j < 0:
            return empty
        try:
            data = json.loads(s[i:j + 1])
        except Exception:
            return empty
        return parse_tcl_spec_json(data, self.code, self.name, series, model, url)
