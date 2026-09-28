"""Amazon Belgium 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 200
- 产品数: 31
- 状态: reachable
- 同构品牌：复用 amazon_common（Amazon 各国前端同构：div[data-asin] 候选 + data-hook 价格/评论）
"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class AmazonBeAdapter(SiteAdapter):
    code = "amazon_be"
    name = "Amazon Belgium"
    base_url = "https://www.amazon.com.be"
    country = "Belgium"
    channel = "Amazon"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 8.0
    SEARCH_PATH = "/s?k={q}"

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
