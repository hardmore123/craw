"""TCL 智利官网（tclstore.cl）零售适配器：URL 规则 + 能力标记。

实测（2026-09-27）：
- 平台 Shopify，collection 页 /collections/tv 分页 ?page=N，商品页 /products/<slug>。
- 200 OK 可达，但页面内嵌反爬脚本含 'Access Denied' 字样（指纹检测 JS，仅对
  机器人触发时才替换 body；正常加载 body 是真实商品列表）→ BlockDetector 误判，
  必须加 block_marker_allowlist=('access denied',) 豁免，否则把可达页判成 blocked。
- 价格在 .productitem__price .money（CLP，$ 前缀，. 千分位），有"on sale"划线原价。
- 评价聚合在 aria-label（如 "4 estrellas, 2 reseñas"），无单条评论明细。
字段抽取由 RetailSpec（schemas/retail_specs/tcl_cl.retail.json）驱动。
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class TCLClAdapter(SiteAdapter):
    code = "tcl_cl"
    name = "TCL（智利）"
    base_url = "https://tclstore.cl"
    country = "Chile"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, .productitem__price"
    suggested_interval = 6.0
    # ★ 页面内嵌反爬指纹检测脚本含 'access denied' 字样（仅对机器人触发时替换
    #   body；正常加载时 body 是真实商品列表），BlockDetector 误判拦截 → 必须豁免。
    #   该豁免不影响真实封锁判定：真实封锁页正文极小且无商品链接，靠候选 0 + 状态
    #   码/标题兜底即可区分。
    block_marker_allowlist = ("access denied",)
    SEARCH_PATH = "/collections/tv"

    def search_url(self, keyword: str, page: int = 1) -> str:
        url = self.base_url.rstrip("/") + self.SEARCH_PATH
        if page and page > 1:
            url += f"?page={page}"
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
