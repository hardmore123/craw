"""Amazon 墨西哥站适配器。

Amazon 各国站 DOM 高度一致，但 MX 搜索结果当前不再稳定提供 data-asin 卡片，
因此按真实 /dp/<ASIN> 链接解析候选。价格与评价仍按低频浏览器路径同步/补页抓取。
防护等级 L3（强）：规模化前必须保持单型号、低频并确认授权线路。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import L3_STRONG, SearchHit, ProductPayload
from .. import SiteAdapter
from ..amazon_common import parse_amazon_product as _parse_amazon_product

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?#]|$)", re.I)


class AmazonMxAdapter(SiteAdapter):
    code = "amazon_mx"
    name = "Amazon México"
    base_url = "https://www.amazon.com.mx"
    country = "Mexico"
    channel = "Amazon"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "#productTitle, #dp, .a-price"
    suggested_interval = 8.0        # 强防护站点，宁慢不封
    # 登录校验探针：未登录访问必跳 /ap/signin，已登录才 200。
    # 与 amazon_ca/us 一致——用它判断"是否真登录"，避免把匿名可访问的商品页当成功。
    login_probe_url = "https://www.amazon.com.mx/gp/css/order-history"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"k": str(keyword or "").strip()}, safe="")
        url = f"{self.base_url}/s?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    @classmethod
    def _asin(cls, value: str) -> str:
        text = str(value or "").strip()
        match = _ASIN_RE.search(text)
        if match:
            return match.group(1).upper()
        if re.fullmatch(r"[A-Z0-9]{10}", text, re.I):
            return text.upper()
        return ""

    def product_url(self, sku: str) -> str:
        asin = self._asin(sku)
        if asin:
            return f"{self.base_url}/dp/{asin}"
        value = str(sku or "").strip()
        if value.startswith("http"):
            return value
        return f"{self.base_url}/dp/{value}"

    def parse_search(self, dom, html: str = "", limit: int = 40) -> list[SearchHit]:
        """优先按结果卡解析，避免把同一商品的图片/评分/价格链接重复计数。"""
        if dom is None:
            return []
        hits: list[SearchHit] = []
        seen: set[str] = set()
        selectors = (
            "div[data-component-type='s-search-result']",
            "div[data-asin]",
            "a[href*='/dp/']",
        )
        for selector in selectors:
            for block in dom.sub(selector, limit=max(limit * 8, limit)):
                asin = self._asin(block.self_attr("data-asin"))
                href = block.self_attr("href")
                if not asin:
                    for link in block.sub("a[href*='/dp/']", limit=20):
                        asin = self._asin(link.self_attr("href"))
                        if asin:
                            href = link.self_attr("href")
                            break
                if not asin or asin in seen:
                    continue
                title = (
                    block.text("h2 span")
                    or block.text("h2")
                    or block.text("[data-cy='title-recipe'] span")
                    or block.self_attr("aria-label")
                    or block.self_attr("title")
                    or block.self_text()
                )
                title = " ".join((title or "").split())
                if not title:
                    continue
                seen.add(asin)
                hits.append(SearchHit(
                    sku=asin,
                    url=self.product_url(asin),
                    title=title,
                    rank=len(hits) + 1,
                ))
                if len(hits) >= limit:
                    return hits
        return hits

    def reviews_url(self, sku: str, page: int = 1) -> str:
        """独立评价页。Amazon 对未登录用户的评价翻页深度有限制。"""
        asin = self._asin(sku) or str(sku or "").strip()
        url = f"{self.base_url}/product-reviews/{asin}/?reviewerType=all_reviews"
        if page and page > 1:
            url += f"&pageNumber={int(page)}"
        return url

    # ---- 商品页：价格 + 评分 + 网评（共用实现，见 ..amazon_common）----
    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return _parse_amazon_product(
            dom, site_code=self.code, sku=sku, url=url,
            currency="MXN",
            product_url=self.base_url + "/dp/{sku}")
