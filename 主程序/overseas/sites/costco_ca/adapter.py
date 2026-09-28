"""Costco 加拿大零售站适配器（价格 + 评分 + 网评）。

防护等级 L3。2026-09 实测（经北美出口代理）：
- 搜索页 `https://www.costco.ca/s?keyword=<kw>`（旧的 `/CatalogSearch?keyword=`
  会 301 到这里）。**搜索结果是 Next.js 客户端渲染**，静态 HTML 里一个商品链接
  都没有（"Hisense" 字样出现 0 次），必须用真实浏览器等渲染；渲染完成后
  `a[href*='.product.']` 稳定出现（Hisense tv 实测 24 条）。
  搜索引擎每次要 5 秒以上 + 滚动才出全，所以 spec 里配 `search.wait_ms`。
- 商品页 `/<slug>.product.<productId>.html` **是服务端渲染的**，纯 HTTP 就能拿到：
  JSON-LD `offers.price` / `priceCurrency` / `availability` / `aggregateRating`
  （实测 55" U7SG：$1197.99 CAD、4.38 分、8 条）。
- 价格选择器（`[data-testid*='price']`、`span[automation-id=...]`）实测全部 0 命中，
  价格只能走 JSON-LD —— 别再往价格选择器上加配置。
- 页面**标题里没有完整型号**（写的是 `55" Class - U7SG Series`），真实型号在
  页头 `Item <n> | Model <code>` 与规格表 `Model` 行；两者可能不一致，所以
  两个都收进 `model_candidates` 做精确校验（见 sites/costco_common.py）。
- 网评明细 DOM 里没有（`.bv-content-item` 恒为 0），走 BazaarVoice 客户端接口，
  见 overseas/bv_reviews.py。BV 是**按系列聚合**的，评价会串尺寸，需要溯源。

价格对会员/登录的依赖：实测未登录也能拿到 JSON-LD 价格，但 Costco 有部分商品
隐藏价格，缺失时按 no_price 记，不要用其它字段硬凑。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter
from ..costco_common import enrich_costco_payload

_PROD_RE = re.compile(r"\.product\.(\d+)\.html", re.I)


class CostcoCaAdapter(SiteAdapter):
    code = "costco_ca"
    name = "Costco Canada"
    base_url = "https://www.costco.ca"
    country = "Canada"
    channel = "Costco"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 10.0
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    # 搜索页前 8000 字符里没有 captcha；站点配置 JSON 里出现的 grecaptcha
    # （1.8MB 处）不在 BlockDetector 的扫描窗口内，无需豁免。这里显式声明
    # 是为了以后 DOM 变化时不必改核心代码。
    block_marker_allowlist = ("captcha",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"keyword": keyword})
        url = f"{self.base_url}/s?{q}"
        if page and page > 1:
            url += f"&currentPage={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # sku 为 product id；无 slug 时 Costco 用 .product.<id>.html（会 301 到带 slug 的 URL）
        return f"{self.base_url}/.product.{sku}.html"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='.product.']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        payload = jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel,
            default_currency="CAD")
        html = ""
        try:
            html = dom.html() if dom is not None else ""
        except Exception:
            html = ""
        return enrich_costco_payload(payload, html, url)
