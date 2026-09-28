"""Todohogar 极薄适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class TodohogarAdapter(SiteAdapter):
    code = "todohogar_ec"
    name = "Todohogar"
    base_url = "https://www.todohogar.com"
    country = "Ecuador"
    channel = "Todohogar"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 6.0
    SEARCH_PATH = "/buscar?q={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        if page and page > 1:
            url += "&page={}".format(page)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v