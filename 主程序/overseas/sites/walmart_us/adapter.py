"""Walmart 美国站适配器（价格 + 网评同页；需美国 IP / 美国住宅代理）。

防护等级 L3（强，PerimeterX）。实测（2026-09-20，本机出口为美国 IP 170.178.173.10）：
- 首页 https://www.walmart.com/ → 200，含 __NEXT_DATA__ 与 2 个 JSON-LD 块
- 搜索页 https://www.walmart.com/search?q=<kw> → 200 但**返回 PerimeterX 验证页**
  （"Robot or human?"，仅 15KB，无 __NEXT_DATA__）→ 普通 HTTP 直连拿不到搜索候选。

商品页 URL 规律：/ip/<slug>/<itemId>（/ip/<itemId> 会重定向到带 slug 的正式 PDP）
搜索页：/search?q=<kw>；商品链接形如 /ip/.../<digits>

★ 两个设计决定：

1) **抽取走 spec 驱动**（`spec_driven_detail = True`）
   字段定义在 `docs/retail_specs/walmart_us.retail.json`（六段：product/price/
   summary/reviews），站点代码只留 URL 规则，与 MX/南美线一致。
   `parse_product` 仍保留，作为**未传 spec 时**的兜底实现。

2) **不加 block_marker_allowlist（已实测确认不需要）**
   首页完整 HTML 里确实含 `perimeterx` / `captcha` 字样（PerimeterX bootstrap 脚本），
   但实测**不在前 8000 字符内**——而 `BlockDetector` 只扫 `title + html[:8000]`
   （见 `infra.py` 的 `check()`），所以首页不会误报；拦截页则把 `robot or human`
   放在前 8000 字符内，能被稳定识别。当前证据下**严格识别即可，无需豁免**。
   保留此说明的理由：正常 search/PDP 页在拿到住宅代理前无法实测；若届时发现它们
   在前 8000 字符内也带 PX 字样，需重新评估——豁免的前提是拦截页仍能被
   `robot or human` 单点识别（漏判会让整站型号被静默记成 `no_item`，比误报 blocked 危害更大）。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter
from ..walmart_common import enrich_walmart_payload

# /ip/<slug>/<itemId> 或 /ip/<itemId>
_PROD_RE = re.compile(r"/ip/(?:[^/]+/)?(\d{5,})", re.I)


class WalmartUsAdapter(SiteAdapter):
    code = "walmart_us"
    name = "Walmart US"
    base_url = "https://www.walmart.com"
    country = "USA"
    channel = "Walmart"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    # 商品页抽取交给 RetailSpec 六段定义（JSON-LD 稳定的站，无需站点解析代码）
    spec_driven_detail = True
    suggested_interval = 10.0        # 强防护站点，宁慢不封
    retail_page_wait = "h1, script[type='application/ld+json']"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"q": keyword})
        url = f"{self.base_url}/search?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # sku 为 itemId（纯数字），/ip/<itemId> 会重定向到带 slug 的正式 PDP
        return f"{self.base_url}/ip/{sku}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return f"{self.base_url}/reviews/product/{sku}?page={int(page or 1)}"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        """从搜索结果链接取候选，并保留卡片标题供型号匹配。

        纯 itemId 不能证明搜索结果就是目标型号，标题必须一路带到
        `match_product` 做词边界/主干校验（宁缺毋滥）。
        """
        return search_link_hits(dom, html, "a[href*='/ip/']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        """未传 RetailSpec 时的兜底抽取：JSON-LD 为主，CSS 取价带护栏。"""
        payload = jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel,
            default_currency="USD")
        html = ""
        try:
            html = dom.html() if dom is not None else ""
        except Exception:
            html = ""
        return enrich_walmart_payload(payload, dom=dom, html=html,
                                      url=url or self.product_url(sku))

    def enrich_payload(self, payload, dom=None, html: str = "", url: str = ""):
        """spec 驱动抽取后的补强：权威型号候选 + 尺寸 + 带护栏的取价兜底。

        没有这一步，spec 路径下 `model_candidates` 恒为空，详情页型号校验会退化成
        宽松子串匹配（`_payload_matches_model` 的第 2 条路）。
        """
        return enrich_walmart_payload(payload, dom=dom, html=html, url=url)
