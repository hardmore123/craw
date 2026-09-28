"""海信日本 TV 官网 SPEC 适配器。

实测结构（2026-09）：
- 入口 https://www.hisense.co.jp/tv/ 列出各系列链接 /tv/<series>/
- SPEC 页不只使用 d6.php；按系列可能使用 d6.php、d5.php 或 d1.php
- 页面通常包含单个 <table>，表解析交给
  spec_parser.parse_hisense_table（处理区分 rowspan、机型对齐、中文翻译）

只做 SPEC 采集，不支持关键词搜索/评价（那是电商站的职责）。
"""
from __future__ import annotations

import re

from ...models import L1_MILD, SpecSheet
from ...spec_parser import parse_hisense_table
from ...fetchers import Dom
from .. import SiteAdapter

_SERIES_RE = re.compile(r'href="(/tv/([a-z][a-z0-9]+)/)"', re.I)
# 入口页里这些是非产品链接，排除
_NON_SERIES = {"tv", "audio", "refrigerator", "washer", "aircon", "search-manual"}


class HisenseJpAdapter(SiteAdapter):
    code = "hisense_jp"
    name = "Hisense（日本）"
    base_url = "https://www.hisense.co.jp"
    country = "日本"
    channel = "Hisense 官网"
    protection = L1_MILD          # 品牌官网，温和；但页面靠 JS，需浏览器
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True

    ENTRY = "https://www.hisense.co.jp/tv/"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def list_series(self, dom: Dom | None, html: str = "") -> list[tuple[str, str]]:
        """从入口页解析系列 → [(系列名, SPEC页URL)]。"""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for m in _SERIES_RE.finditer(html or ""):
            path, code = m.group(1), m.group(2).lower()
            if code in _NON_SERIES or code in seen:
                continue
            seen.add(code)
            spec_url = f"{self.base_url}{path}d6.php"
            out.append((code.upper(), spec_url))
        return out

    def series_page_url(self, series: str) -> str:
        return f"{self.base_url}/tv/{series.lower()}/"

    def spec_url(self, series: str) -> str:
        return f"{self.base_url}/tv/{series.lower()}/d6.php"

    def spec_url_candidates(self, series: str, primary_url: str = "") -> list[str]:
        """返回官网可能使用的 SPEC 页面，按新版到旧版顺序尝试。"""
        base = f"{self.base_url}/tv/{series.lower()}"
        candidates = [primary_url, f"{base}/d6.php", f"{base}/d5.php", f"{base}/d1.php"]
        return list(dict.fromkeys(url for url in candidates if url))

    def parse_spec(self, dom: Dom | None, series: str, url: str = "") -> SpecSheet:
        if dom is None:
            return SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        tables = dom.sub("table", limit=1)
        if not tables:
            return SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        return parse_hisense_table(tables[0], self.code, self.name, series, url)
