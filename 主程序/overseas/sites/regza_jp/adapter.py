"""REGZA（东芝映像/日本）TV 官网 SPEC 适配器。

实测结构（2026-09）：
- lineup 入口 https://www.regza.com/tv/lineup 列出各系列 /tv/lineup/<系列>
- SPEC 页 = /tv/lineup/<系列>/spec，页内多个分组 <table>（每表 2 列 td：项目|值），
  每个 table 前置最近的 h2/h3/h4 是分组标题（区分）。值不分尺寸机型。
- 型号（如 55X9900R/65X9900R）以叶子元素散落在页面，用 JS 一次性抽取。

解析走 spec_parser.parse_regza_from_data（需 DOM 支持 eval_js，即浏览器路径）。
"""
from __future__ import annotations

import re

from ...models import L1_MILD, SpecSheet
from ...spec_parser import regza_extract_js, parse_regza_from_data
from ...fetchers import Dom
from .. import SiteAdapter

# /tv/lineup/<系列>  系列码形如 e350m / z890s / x9900r / zx3s
_SERIES_RE = re.compile(r"/tv/lineup/([a-z][a-z0-9]{2,10})(?:[/?#]|$)", re.I)
_NON_SERIES = {"selection-guide", "spec"}


class RegzaJpAdapter(SiteAdapter):
    code = "regza_jp"
    name = "REGZA（日本）"
    base_url = "https://www.regza.com"
    country = "日本"
    channel = "REGZA 官网"
    protection = L1_MILD
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/tv/lineup/']"
    # 首个系列链接出现得很早，其余系列由懒加载继续注入；入口需额外等待稳定。
    spec_entry_extra_wait_ms = 5000
    spec_page_wait = "table"

    ENTRY = "https://www.regza.com/tv/lineup"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def list_series(self, dom: Dom | None, html: str = "") -> list[tuple[str, str]]:
        """从 lineup 页解析在售系列 → [(系列码, SPEC页URL)]。"""
        hrefs: list[str] = []
        if dom is not None:
            hrefs = dom.attr_list("a[href*='/tv/lineup/']", "href", limit=300)
        hrefs += re.findall(r'href="([^"]*?/tv/lineup/[^"]*)"', html or "")

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href in hrefs:
            m = _SERIES_RE.search(href or "")
            if not m:
                continue
            code = m.group(1).lower()
            if code in _NON_SERIES or code in seen:
                continue
            seen.add(code)
            out.append((code.upper(), f"{self.base_url}/tv/lineup/{code}/spec"))
        return out

    def series_page_url(self, series: str) -> str:
        return f"{self.base_url}/tv/lineup/{series.lower()}"

    def spec_url(self, series: str) -> str:
        return f"{self.base_url}/tv/lineup/{series.lower()}/spec"

    def parse_spec(self, dom: Dom | None, series: str, url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        if dom is None:
            return empty
        eval_js = getattr(dom, "eval_js", None)
        if not callable(eval_js):
            return empty                     # REGZA 必须浏览器路径
        # 型号 = <尺寸数字><系列码>，把系列码内联进抽取脚本精确匹配
        data = eval_js(regza_extract_js(series))
        if not data:
            return empty
        return parse_regza_from_data(data, self.code, self.name, series, url)
