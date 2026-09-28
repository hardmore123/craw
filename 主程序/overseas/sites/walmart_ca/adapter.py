"""Walmart 加拿大零售站适配器（价格 + 网评同页）。

防护等级 L3（强，Akamai/PerimeterX）。实测线索（2026-09）：
- 搜索页 https://www.walmart.ca/en/search?q=<kw>，商品页 /en/ip/<slug>/<itemId>。
- PDP 数据主要在 __NEXT_DATA__ / JSON-LD；价格/评分优先 JSON-LD 兜底。
规模化抓取需加拿大住宅代理 + 可能验证码；regions_config 默认 enabled=False。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter

_PROD_RE = re.compile(r"/ip/(?:[^/]+/)?(\d{5,})", re.I)


class WalmartCaAdapter(SiteAdapter):
    code = "walmart_ca"
    name = "Walmart Canada"
    base_url = "https://www.walmart.ca"
    country = "Canada"
    channel = "Walmart"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 10.0
    supports_retail = True
    retail_page_wait = "script[type='application/ld+json'], h1"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"q": keyword})
        url = f"{self.base_url}/en/search?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        return f"{self.base_url}/en/ip/{sku}"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='/ip/']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel)
