"""Richer Sounds 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class RichersoundsUkAdapter(SiteAdapter):
    code = "richersounds_uk"
    name = "Richer Sounds"
    base_url = "https://www.richersounds.com"
    country = "United Kingdom"
    channel = "Richer Sounds"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, body"
    suggested_interval = 6.0
    SEARCH_PATH = "/search/?q={q}"

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
