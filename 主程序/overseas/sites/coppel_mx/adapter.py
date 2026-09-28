"""Coppel 墨西哥零售站适配器（价格 + 网评，同一商品页同步抓取）。

站内搜索 → 商品详情页；价格与评价都在商品页，reviews_url 返回空串。
选择器/URL 为「以实测为准」初值。防护 L2，需浏览器。
"""
from __future__ import annotations

import urllib.parse

from ...models import L2_MEDIUM
from .. import SiteAdapter


class CoppelMxAdapter(SiteAdapter):
    code = "coppel_mx"
    name = "Coppel"
    base_url = "https://www.coppel.com"
    country = "Mexico"
    channel = "Coppel"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 5.0

    def search_url(self, keyword: str, page: int = 1) -> str:
        # Coppel 的搜索框实际导航到 /sd/<关键词>；/buscar?q= 会返回 404。
        q = urllib.parse.quote(str(keyword or "").strip(), safe="")
        url = f"{self.base_url}/sd/{q}"
        if page and page > 1:
            url += f"?page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # 搜索卡片当前返回绝对 /pdp/ URL，同时兼容根相对 href。
        value = str(sku or "").strip()
        if value.startswith("http"):
            return value
        if value.startswith("/"):
            return f"{self.base_url.rstrip('/')}{value}"
        if value.startswith("pdp/"):
            return f"{self.base_url}/{value}"
        return f"{self.base_url}/pdp/{value.lstrip('/')}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""
