"""Leclerc 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- 首页 https://www.e.leclerc HTTP 200（仅促销位，非电视列表）
- 电视分类 /c/multimedia/televiseurs 与搜索 /recherche 均返回 404 'Page introuvable'
- 状态: blocked（公开 URL 拿不到电视列表，需登录态/住宅IP）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class LeclercFrAdapter(SiteAdapter):
    code = "leclerc_fr"
    name = "Leclerc"
    base_url = "https://www.e.leclerc"
    country = "France"
    channel = "Leclerc"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/c/multimedia/televiseurs"

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
