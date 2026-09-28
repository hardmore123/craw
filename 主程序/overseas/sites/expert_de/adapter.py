"""Expert DE 极薄适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class ExpertDEAdapter(SiteAdapter):
    code = "expert_de"
    name = "Expert DE"
    base_url = "https://www.expert.de"
    country = "Germany"
    channel = "Expert DE"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 6.0
    # /shop/search?query= 返回404，改用分类页 /fernseher
    SEARCH_PATH = "/fernseher"

    def search_url(self, keyword: str, page: int = 1) -> str:
        url = self.base_url.rstrip("/") + self.SEARCH_PATH
        if page and page > 1:
            url += "?page={}".format(page)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
