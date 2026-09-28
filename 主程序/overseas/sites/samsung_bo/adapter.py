"""Samsung 玻利维亚官网（samsung.com.bo）零售适配器：URL 规则 + 能力标记。

实测（2026-09-27）：
- Samsung 自建 AEM 站，分类页 /tvs 与子分类 /search/tvs/<subtype> 列 product-card-v2 卡片。
- 200 OK 可达（偶超时需 ≥45s nav_timeout）。商品页 /tvs/<category>/<slug>-<modelcode>，
  型号 modelcode 如 UN75AU8200GXZS（JSON-LD Product.sku 含）。
- ★ 品牌展示型 "where-to-buy" 站，**无在线直接售价**：PDP data-price 全是 Angular 模板
  占位（upgradeResult.dataPrice 等）非渲染值；文案 "Los precios en tiendas y de otros
  minoristas varían"。按"缺价 > 错价"原则 price 选择器配齐抓不到即留空，capabilities 仍含
  price（200 OK 可达）。
- DB site 记录已存在（protection=L3_STRONG，之前补抓 15 产品），本任务只补 spec/adapter。
字段抽取由 RetailSpec（schemas/retail_specs/samsung_bo.retail.json）驱动。
"""
from __future__ import annotations

from ...models import L3_STRONG
from .. import SiteAdapter


class SamsungBoAdapter(SiteAdapter):
    code = "samsung_bo"
    name = "Samsung（玻利维亚）"
    base_url = "https://samsung.com.bo"
    country = "Bolivia"
    channel = "Samsung 官网"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, .product-card-v2"
    suggested_interval = 7.0
    SEARCH_PATH = "/tvs"

    def search_url(self, keyword: str, page: int = 1) -> str:
        return self.base_url.rstrip("/") + self.SEARCH_PATH

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
