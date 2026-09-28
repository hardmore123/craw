"""Tottus PE 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-26：
- HTTP 状态: -1
- 状态: blocked
- 同构品牌：复用 falabella 集团系（tottus 为智利 Falabella 旗下商超，搜索结构与 falabella 类似）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class TottusPeAdapter(SiteAdapter):
    code = "tottus_pe"
    name = "Tottus PE"
    base_url = "https://www.tottus.com.pe"
    country = "Peru"
    channel = "Tottus"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/tottus-pe/search?text={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        if page and page > 1:
            url += "{}page={}".format("&" if "?" in url else "?", page)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
