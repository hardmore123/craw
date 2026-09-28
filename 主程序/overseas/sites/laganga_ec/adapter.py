"""L-Ganga 极薄适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class LGangaAdapter(SiteAdapter):
    code = "laganga_ec"
    name = "L-Ganga"
    base_url = "https://laganga.com"
    country = "Ecuador"
    channel = "L-Ganga"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 6.0
    SEARCH_PATH = "/televisores"

    def search_url(self, keyword: str, page: int = 1) -> str:
        url = self.base_url.rstrip("/") + self.SEARCH_PATH
        if page and page > 1:
            url += "?p={}".format(page)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v