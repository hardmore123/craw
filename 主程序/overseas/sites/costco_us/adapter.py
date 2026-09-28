"""Costco 美国站适配器（价格 + 评分 + 网评）。

防护等级 L3。2026-09 实测：
- 非美国出口 IP 会拿到 403 Access Denied（**IP 层拦截**，与请求头无关）。
  用美国出口的住宅/机房代理都能通；本机实测走的代理出口为美国 IP，
  `https://www.costco.com/s?keyword=Hisense+tv` 返回 200 且渲染出 24 个
  `.product.` 候选。所以 costco_us 的可用性完全取决于有没有美国线路。
- 搜索页 `https://www.costco.com/s?keyword=<kw>`（`/CatalogSearch?keyword=`
  会 301 到这里）。结果是 Next.js 客户端渲染，必须用真实浏览器 + 滚动 + 等待。
- 商品页 `<slug>.product.<productId>.html`，服务端渲染，JSON-LD 与 CA 同构；
  型号同样只出现在页头 `Item <n> | Model <code>` 与规格表 `Model` 行。
- 网评同样走 BazaarVoice（客户端 Costco-US / displayCode 需在有美国线路时
  用 scripts/diag_retail_network.py 复核后再填进 spec）。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter
from ..costco_common import enrich_costco_payload

_PROD_RE = re.compile(r"\.product\.(\d+)\.html", re.I)


class CostcoUsAdapter(SiteAdapter):
    code = "costco_us"
    name = "Costco US"
    base_url = "https://www.costco.com"
    country = "USA"
    channel = "Costco"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 10.0
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    block_marker_allowlist = ("captcha",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"keyword": keyword})
        url = f"{self.base_url}/s?{q}"
        if page and page > 1:
            url += f"&currentPage={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # sku 为 productId；Costco PDP 需 slug，用 .product.<id>.html 直达通常可重定向
        return f"{self.base_url}/.product.{sku}.html"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='.product.']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        payload = jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel,
            default_currency="USD")
        html = ""
        try:
            html = dom.html() if dom is not None else ""
        except Exception:
            html = ""
        return enrich_costco_payload(payload, html, url)

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""
