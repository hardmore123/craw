"""Best Buy 加拿大零售站适配器（价格 + 网评同页；★必须加拿大可通行出口 IP）。

防护等级 L3（强）。**2026-09-20 本机实测的准确事实**：

| 路径 | 结果 |
|---|---|
| `/robots.txt`、`/sitemap_index.xml`、`/sitemapN.xml.gz` | ✅ 200，可下载 |
| `/en-ca`、`/en-ca/category/...`、`/en-ca/product/...` | ❌ **403 "Access Denied"（正文仅 297~324 字节）** |

★关键：403 在**真实浏览器（Edge，含 stealth 指纹）**下同样出现，所以
**不是缺请求头、不是要 JS 渲染、也不是验证码**，而是站点边缘按 IP/ASN 的硬拦。
→ 价格/网评的 DOM 抽取**目前在本机无法校准**，需加拿大可通行的出口 IP。

**★合规（决定了抓取路径，必读）**：`robots.txt` 明确
`Disallow: /en-ca/search` 与 `Disallow: /en-ca/Search`（**站内搜索页禁抓**），
同时**显式放行** `Allow: /en-ca/product/`、`/en-ca/category/`、`/en-ca/brand/`，
并公布 `Sitemap: https://www.bestbuy.ca/sitemap_index.xml`。

→ 因此本适配器的 `search_url()` **不应用于生产抓取**，只为框架兼容保留。
正确入口是 **sitemap 商品目录**：`scripts/bestbuy_ca_catalog.py` 把 38 个 sitemap
解析成本地目录（型号 → SKU → PDP URL），再由 `scripts/crawl_bestbuy_direct.py`
直连 PDP。这样既绕开被禁的搜索路径，又把"某型号有没有上架"从模糊搜索问题
变成**确定性存在性问题**。

URL 形态：搜索 `/en-ca/search?search=<kw>&page=<n>`（禁抓）；
商品页 `/en-ca/product/<slug>/<sku>`（sku 为纯数字，slug 不承载身份）。

抽取路径：商品页嵌 JSON-LD Product/Offer/AggregateRating，走
`retail_common.jsonld_product_payload`（title/brand/model/price/评分/内嵌评价）。
评价后端（是否 BazaarVoice）**未验证**——旧注释称走 BV，但姊妹站 bestbuy.com
在 2022-12 后已不再请求 BV 域名，所以这条必须实测，不能照抄。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, ProductPayload
from ...retail_common import jsonld_product_payload, search_link_hits
from .. import SiteAdapter

_PROD_RE = re.compile(r"/product/[^/]+/(\d+)", re.I)


class BestBuyCaAdapter(SiteAdapter):
    code = "bestbuy_ca"
    name = "Best Buy Canada"
    base_url = "https://www.bestbuy.ca"
    country = "Canada"
    channel = "Best Buy"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 9.0
    supports_retail = True
    retail_page_wait = "script[type='application/ld+json'], .productPricingContainer, h1"

    # ★robots.txt 禁抓 /en-ca/search；生产路径走 sitemap。保留此开关是为了让
    #   上层（与文档）能显式看到"这条路不该走"，而不是靠口头约定。
    search_allowed_by_robots = False

    def search_url(self, keyword: str, page: int = 1) -> str:
        """★robots.txt 明确 Disallow: /en-ca/search —— 不要用于生产抓取。

        仅用于：离线样本采集、人工核对、以及既有框架的兼容调用。
        合规入口见模块 docstring（sitemap 目录 + scripts/crawl_bestbuy_direct.py）。
        """
        q = urllib.parse.urlencode({"search": keyword})
        url = f"{self.base_url}/en-ca/search?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str, slug: str = "") -> str:
        """商品页 URL。★slug 不承载身份（SKU 才是），留空也能直达。

        带 slug 更稳（Best Buy 会把商品名放进 slug，某些边缘规则或跳转更友好）。
        sitemap 目录里 slug 与 sku 是现成的，直接传进来即可。
        """
        if slug:
            return f"{self.base_url}/en-ca/product/{slug}/{sku}"
        return f"{self.base_url}/en-ca/product/{sku}"

    def parse_search(self, dom, html: str = "", limit: int = 40):
        return search_link_hits(dom, html, "a[href*='/product/']",
                                _PROD_RE, self.base_url, limit=limit)

    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return jsonld_product_payload(
            dom, sku=sku, url=url or (self.product_url(sku) if sku else ""),
            site_code=self.code, channel=self.channel)
