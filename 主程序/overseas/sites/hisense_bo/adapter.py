"""Hisense 玻利维亚官网（hisense.com.bo）零售适配器：URL 规则 + 能力标记。

实测（2026-09-27）：
- 平台 WordPress + Bricks builder（非标准 WooCommerce 商品卡），产品页 /product/tv/<cat>/<slug>/。
- 首页 /product-category/tv/ 是子分类导览（MiniLED/QLED/Canvas），产品需进子分类页
  /product-category/tv/<qled|miniled|canvas>/ 才出现。200 OK 可达。
- ★ 该站是品牌展示型，**无在线价格**（PDP 无 woocommerce-Price/data-price-amount/Bs/$），
  按"缺价 > 错价"原则，price 选择器配齐但抓不到时留空不造假。capabilities 仍含 price
  （200 OK 可达），执行层抓不到价即 price 字段为空。
- 型号在产品 URL slug（如 tv-85q6n-google-tv-qled-smart → 85Q6N）+ img alt。
字段抽取由 RetailSpec（schemas/retail_specs/hisense_bo.retail.json）驱动。
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class HisenseBoAdapter(SiteAdapter):
    code = "hisense_bo"
    name = "Hisense（玻利维亚）"
    base_url = "https://hisense.com.bo"
    country = "Bolivia"
    channel = "Hisense 官网"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, .product"
    suggested_interval = 6.0
    SEARCH_PATH = "/product-category/tv/"

    def search_url(self, keyword: str, page: int = 1) -> str:
        url = self.base_url.rstrip("/") + self.SEARCH_PATH
        if page and page > 1:
            url += f"page/{page}/"
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
