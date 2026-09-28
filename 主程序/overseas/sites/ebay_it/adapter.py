"""eBay Italy 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 403
- 产品数: 0
- 状态: blocked
- 同构品牌：复用 ebay_fr/ebay_common（eBay 各国同构：/sch/i.html?_nkw= 搜索，li.s-item 候选）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class EbayItAdapter(SiteAdapter):
    code = "ebay_it"
    name = "eBay Italy"
    base_url = "https://www.ebay.it"
    country = "Italy"
    channel = "eBay Italy"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/sch/TV-Video/11071/i.html?_nkw={q}"

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
