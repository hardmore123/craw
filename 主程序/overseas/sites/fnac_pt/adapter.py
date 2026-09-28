"""Fnac PT 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 403
- 产品数: 0
- 状态: blocked
- 同构品牌：复用 fnac_fr（Fnac 各国同构：/SearchResult 搜索，L2）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class FnacPtAdapter(SiteAdapter):
    code = "fnac_pt"
    name = "Fnac PT"
    base_url = "https://www.fnac.pt"
    country = "Portugal"
    channel = "Fnac"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/Televisores"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
