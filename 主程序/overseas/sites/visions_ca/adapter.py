"""Visions Electronics 加拿大零售站适配器（价格 + 网评同页）。

防护等级 L2（中）。实测线索（2026-09）：搜索页
https://www.visions.ca/catalogsearch/result/?q=<kw>（Magento 平台），商品页 /<slug>.html。
Magento 商品页通常嵌 JSON-LD Product/Offer/AggregateRating；优先 JSON-LD 兜底。
选择器/URL「以实测为准」；regions_config 默认 enabled=False。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L2_MEDIUM, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter

_PROD_RE = re.compile(r"/([a-z0-9\-]+)\.html", re.I)


class VisionsCaAdapter(SiteAdapter):
    code = "visions_ca"
    name = "Visions Electronics"
    base_url = "https://www.visions.ca"
    country = "Canada"
    channel = "Visions"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 6.0
    supports_retail = True
    retail_page_wait = "script[type='application/ld+json'], h1"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"q": keyword})
        url = f"{self.base_url}/catalogsearch/result/?{q}"
        if page and page > 1:
            url += f"&p={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        if sku.startswith("http"):
            return sku
        return f"{self.base_url}/{sku}.html"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a.product-item-link",
                                _PROD_RE, self.base_url, limit=limit,
                                skip_skus={"index", "home"})

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel)
