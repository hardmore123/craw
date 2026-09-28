"""La Curacao 极薄适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class LaCuracaoAdapter(SiteAdapter):
    code = "lacuracao_pe"
    name = "La Curacao"
    base_url = "https://www.lacuracao.pe"
    country = "Peru"
    channel = "La Curacao"
    protection = L2_MEDIUM
    requires_browser = False
    supports_retail = True
    retail_page_wait = "h1"
    _magento_ssr = True  # Magento 服务端渲染，HTTP 直抓即可
    suggested_interval = 6.0
    SEARCH_PATH = "/catalogsearch/result/?q={q}"

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