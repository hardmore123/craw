"""Amazon 加拿大站适配器。

防护等级 L3（强）：必须真实浏览器，且要低频。实测（2026-09）：
- 搜索页 https://www.amazon.ca/s?k=<kw> → HTTP 200，无机器人校验
- 商品页 https://www.amazon.ca/dp/<ASIN> → HTTP 200
- 商品页无 JSON-LD（0 块），所以只能走 CSS 选择器
注意：单次探测通过不代表批量安全，规模化后大概率触发验证码。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import (L3_STRONG, Product, PriceSnapshot, ProductPayload,
                       Review, ReviewSummary, SearchHit)
from .. import SiteAdapter


def _to_price(text: str) -> float | None:
    m = re.search(r"([\d,]+\.\d{2}|[\d,]+)", (text or "").replace("\u00a0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _to_float(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _to_int(text: str) -> int | None:
    m = re.search(r"([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


# ==================== 价格区校验 ====================
# 实现已提到 ..amazon_common（三站共用，避免各站漂移）。此处仅保留引用，
# 详细背景见 amazon_common 模块 docstring（32A4NV 配件价事故）。
from ..amazon_common import parse_amazon_product as _parse_amazon_product  # noqa: E402


class AmazonCaAdapter(SiteAdapter):
    code = "amazon_ca"
    name = "Amazon Canada"
    base_url = "https://www.amazon.ca"
    country = "Canada"
    channel = "Amazon"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 8.0        # 强防护站点，宁慢不封
    supports_retail = True
    retail_page_wait = "#productTitle, #dp, .a-price"
    # 登录校验探针：未登录访问必跳 /ap/signin，已登录才 200。
    # 用它判断"是否真的登录"，避免把匿名可访问的商品页当成登录成功。
    login_probe_url = "https://www.amazon.ca/gp/css/order-history"

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.urlencode({"k": keyword}, safe="")
        url = f"{self.base_url}/s?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        return f"{self.base_url}/dp/{sku}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        """独立评价页。注意 Amazon 对未登录用户的评价翻页深度有限制。"""
        url = f"{self.base_url}/product-reviews/{sku}/?reviewerType=all_reviews"
        if page and page > 1:
            url += f"&pageNumber={int(page)}"
        return url

    # ---- 列表页：从搜索结果取带 ASIN 的商品 ----
    def parse_search(self, dom, html: str = "", limit: int = 40) -> list[SearchHit]:
        if dom is None:
            return []
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for block in dom.sub("div[data-asin]", limit=limit * 2):
            asin = block.self_attr("data-asin").strip()
            if not asin or asin in seen:
                continue
            seen.add(asin)
            title = block.text("h2 span") or block.text("h2")
            hits.append(SearchHit(sku=asin, url=self.product_url(asin),
                                  title=title, rank=len(hits) + 1))
            if len(hits) >= limit:
                break
        return hits

    # ---- 商品页：价格 + 评分 + 网评（共用实现，见 ..amazon_common）----
    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return _parse_amazon_product(
            dom, site_code=self.code, sku=sku, url=url,
            currency='CAD', product_url='https://www.amazon.ca/dp/{sku}')

