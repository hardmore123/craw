"""Canadian Tire 加拿大零售站适配器（价格 + 网评同页）。

防护等级 L3（强）。实测线索（2026-09）：Canadian Tire 价格/库存按门店（需选店/邮编），
未选店可能只显示「查看门店价格」。搜索页 https://www.canadiantire.ca/en/search-results.html?q=<kw>，
商品页 /en/pdp/<slug>-<pcode>p.html（pcode 为 8 位商品号）。
商品页通常嵌 JSON-LD Product/Offer/AggregateRating；价格可能因门店墙缺失，优先 JSON-LD 兜底。
regions_config 默认 enabled=False；需人工确认门店价格可行性后再开。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter

_PROD_RE = re.compile(r"/pdp/([a-z0-9\-]+?)(\d{6,})p\.html", re.I)


class CanadianTireCaAdapter(SiteAdapter):
    code = "canadiantire_ca"
    name = "Canadian Tire"
    base_url = "https://www.canadiantire.ca"
    country = "Canada"
    channel = "Canadian Tire"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 9.0
    supports_retail = True
    retail_page_wait = "script[type='application/ld+json'], h1"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"q": keyword})
        return f"{self.base_url}/en/search-results.html?{q}"

    def product_url(self, sku: str) -> str:
        if sku.startswith("http"):
            return sku
        # sku 为 pcode；无 slug 时用通用 pdp 前缀（站点会补全）
        return f"{self.base_url}/en/pdp/{sku}p.html"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='/pdp/']",
                                _PROD_RE, self.base_url, limit=limit,
                                sku_group=2)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel)
