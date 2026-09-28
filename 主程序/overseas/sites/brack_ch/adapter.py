"""Brack Switzerland 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 200
- 产品数: 0
- 状态: empty
- 独立站结构（探测发现 product_count=0）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class BrackChAdapter(SiteAdapter):
    code = "brack_ch"
    name = "Brack Switzerland"
    base_url = "https://www.brack.ch"
    country = "Switzerland"
    channel = "Brack"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/it-multimedia/tv-heimkino/tv"

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
