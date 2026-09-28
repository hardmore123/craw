"""Liverpool 墨西哥零售站适配器（价格 + 网评，同一商品页同步抓取）。

站内搜索 → 商品详情页；价格与用户评价都在商品页（评价常由 PowerReviews/Bazaarvoice
渲染在页内），因此 reviews_url 返回空串，评价随商品页一并抽取（同步抓取）。

选择器/URL 规则为「以实测为准」的初值：DOM 改版或与实测不符时只需调整本文件与
schema.json，不影响核心框架与其它线。防护 L2，需浏览器。
"""
from __future__ import annotations

import urllib.parse

from ...models import L2_MEDIUM
from .. import SiteAdapter


class LiverpoolMxAdapter(SiteAdapter):
    code = "liverpool_mx"
    name = "Liverpool"
    base_url = "https://www.liverpool.com.mx"
    country = "Mexico"
    channel = "Liverpool"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 12.0  # 实测连续抓 200+ 型号触发 410 限流，加到 12 秒

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.quote(keyword.strip())
        url = f"{self.base_url}/tienda?s={q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # 搜索卡片返回的是相对 PDP href；完整 URL 和相对路径都直接保留。
        value = str(sku or "").strip()
        if value.startswith("http"):
            return value
        if value.startswith("/"):
            return f"{self.base_url.rstrip('/')}{value}"
        return f"{self.base_url}/tienda/pdp/{value}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""       # 评价在商品页，随商品页同步抽取
