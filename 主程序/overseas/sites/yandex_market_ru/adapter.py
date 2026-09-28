"""Yandex Market 极薄适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。
状态: blocked（需俄罗斯IP）。需俄罗斯住宅IP，当前被拦(HTTP 403 Forbidden（地域限制，需俄罗斯IP+Yandex账号态）)。"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class YandexMarketRuAdapter(SiteAdapter):
    code = "yandex_market_ru"
    name = "Yandex Market"
    base_url = "https://market.yandex.ru"
    country = "Russia"
    channel = "Yandex Market"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, body"
    suggested_interval = 10.0
    SEARCH_PATH = "/catalog/televizory/"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
