"""Hisense Perú 品牌官网适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 200
- 可达 200，capabilities=['price']
- 独立站（Nuxt SSR）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class HisensePeAdapter(SiteAdapter):
    code = "hisense_pe"
    name = "Hisense Perú"
    base_url = "https://www.hisense.com.pe"
    country = "Peru"
    channel = "Hisense 官网"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/?s={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        if page and page > 1:
            url += "&page=" + str(int(page))
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
