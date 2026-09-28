"""eMAG RO 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class EmagRoAdapter(SiteAdapter):
    code = "emag_ro"
    name = "eMAG RO"
    base_url = "https://www.emag.ro"
    country = "Romania"
    channel = "eMAG"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/suche/{q}/"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        return self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
