"""Sodimac Perú 零售站适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- HTTP 状态: 200
- 可达 200，capabilities=['price']
- Next.js（Falabella 集团）；robots 禁搜索+禁商品页，spec_driven 保留兼容
"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class SodimacPeAdapter(SiteAdapter):
    code = "sodimac_pe"
    name = "Sodimac Perú"
    base_url = "https://www.sodimac.com.pe"
    country = "Peru"
    channel = "Sodimac"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/sodimac-pe/search?Ntt={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        if page and page > 1:
            url += "&page=" + str(int(page))
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
