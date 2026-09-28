"""Elkjop Norway 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 429
- 产品数: 0
- 状态: blocked
- 同构品牌：复用 elgiganten_dk（Elgiganten/Elkjop 同集团 Dixon：L2，spec_driven_detail）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class ElkjopNoAdapter(SiteAdapter):
    code = "elkjop_no"
    name = "Elkjop Norway"
    base_url = "https://www.elkjop.no"
    country = "Norway"
    channel = "Elkjop"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/category/lcd-led-tv/"

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
