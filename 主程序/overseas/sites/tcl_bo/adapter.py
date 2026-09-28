"""TCL 玻利维亚官网（web.tcl.bo）零售适配器：URL 规则 + 能力标记。

实测（2026-09-27）：
- 自建 Vue 站，搜索页 /search/tv 列 8 个 product-card-item，商品页 /tvs/<MODEL>（如 /tvs/50P755）。
- 200 OK 可达。卡片 data-code="<MODEL>" 是型号（如 50P755/55C655/55C755），URL 同。
- ★ 品牌展示型站，卡片底部仅 "Conoce más" 按钮，**无在线价格**（无 data-price/$/Bs/precio），
  按"缺价 > 错价"原则 price 选择器配齐抓不到即留空，capabilities 仍含 price（200 OK 可达）。
- 标题如 "TCL P755 Classic 4K Smart TV"，型号即标题首段 P755 等。
字段抽取由 RetailSpec（schemas/retail_specs/tcl_bo.retail.json）驱动。
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class TCLBoAdapter(SiteAdapter):
    code = "tcl_bo"
    name = "TCL（玻利维亚）"
    base_url = "https://web.tcl.bo"
    country = "Bolivia"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, .product-card-item"
    suggested_interval = 6.0
    SEARCH_PATH = "/search/tv"

    def search_url(self, keyword: str, page: int = 1) -> str:
        return self.base_url.rstrip("/") + self.SEARCH_PATH

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return self.base_url.rstrip("/") + "/tvs/" + v
