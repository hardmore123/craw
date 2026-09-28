"""Walmart 墨西哥零售站适配器（价格 + 网评，同一商品页同步抓取）。

站内搜索 → 商品详情页；价格与评价都在商品页，reviews_url 返回空串。
Walmart 强防护（L3），需真实浏览器 + 低频；regions_config 默认 enabled=False，
低频小样验证通过后再开。选择器/URL 为「以实测为准」初值。
"""
from __future__ import annotations

import urllib.parse

from ...models import L3_STRONG
from .. import SiteAdapter


class WalmartMxAdapter(SiteAdapter):
    code = "walmart_mx"
    name = "Walmart México"
    base_url = "https://www.walmart.com.mx"
    country = "Mexico"
    channel = "Walmart"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 8.0

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.quote(keyword.strip())
        url = f"{self.base_url}/search?q={q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        if str(sku).startswith("http"):
            return sku
        return f"{self.base_url}/ip/{sku}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""
