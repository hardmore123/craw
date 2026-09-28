"""The Brick 加拿大零售站适配器（价格 + 网评同页）。

防护等级 L2（中）。实测线索（2026-09）：
- 搜索页 https://www.thebrick.com/search?q=<kw>，商品页 /products/<slug>。
- 商品页通常嵌 JSON-LD Product/Offer/AggregateRating；价格/评分优先 JSON-LD 兜底。
选择器/URL「以实测为准」；regions_config 默认 enabled=False，小样本验证后再开。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L2_MEDIUM, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter

_PROD_RE = re.compile(r"/products/([a-z0-9\-]+)", re.I)


class TheBrickCaAdapter(SiteAdapter):
    code = "thebrick_ca"
    name = "The Brick"
    base_url = "https://www.thebrick.com"
    country = "Canada"
    channel = "The Brick"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 6.0
    supports_retail = True
    retail_page_wait = "script[type='application/ld+json'], h1"
    # Shopify 站正常页面内嵌 captcha-bootstrap 脚本，BlockDetector 会误判拦截。
    block_marker_allowlist = ("captcha",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"q": keyword})
        return f"{self.base_url}/search?{q}"

    def product_url(self, sku: str) -> str:
        if sku.startswith("http"):
            return sku
        return f"{self.base_url}/products/{sku}"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='/products/']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel)
