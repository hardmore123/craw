"""Auchan 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- 搜索页 /recherche?text=<kw> HTTP 200，产品 58
- 直链分类页 /high-tech.../televiseur/ca-42850 返回 404（需经搜索重定向）
- 状态: reachable
- 独立站结构
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class AuchanFrAdapter(SiteAdapter):
    code = "auchan_fr"
    name = "Auchan"
    base_url = "https://www.auchan.fr"
    country = "France"
    channel = "Auchan"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/recherche?text={q}"

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
