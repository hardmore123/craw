"""通用适配器：面向发布 schema.org JSON-LD 的站点。

用途：品牌官网/中小零售站往往在商品页嵌 <script type="application/ld+json">，
里面直接有 name / mpn / offers.price / aggregateRating，不需要逐站写选择器。

实测提醒（2026-09）：
- Amazon 商品页没有 JSON-LD，用不了本适配器（用 amazon_ca）。
- TCL / Philips 首页只有 Organization/WebSite，非商品页拿不到 Product。
所以本适配器适合「已知商品 URL」的场景，不支持关键词搜索。

用法：sku 直接传商品页完整 URL。
"""
from __future__ import annotations

from ...models import L1_MILD
from .. import SiteAdapter


class GenericJsonLdAdapter(SiteAdapter):
    code = "generic_jsonld"
    name = "通用 JSON-LD 适配器"
    base_url = ""
    protection = L1_MILD
    # 不需要浏览器：JSON-LD 在原始 HTML 里，纯 HTTP 即可拿到
    requires_browser = False
    suggested_interval = 3.0

    def search_url(self, keyword: str, page: int = 1) -> str:
        raise NotImplementedError(
            "generic_jsonld 不支持关键词搜索，请直接用商品 URL（scenario=detail）")

    def product_url(self, sku: str) -> str:
        """sku 即完整 URL。"""
        if sku.startswith(("http://", "https://")):
            return sku
        raise ValueError(f"generic_jsonld 需要完整商品 URL，收到: {sku!r}")
