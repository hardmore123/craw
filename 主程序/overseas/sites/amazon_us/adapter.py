"""Amazon 美国站适配器（价格 + 网评同页同步抓取）。

防护等级 L3（强）：必须真实浏览器，且要低频。实测（2026-09，本机 Edge）：
- 搜索页 https://www.amazon.com/s?k=<kw> → HTTP 200，返回商品链接 /dp/<ASIN>
- 商品页 https://www.amazon.com/dp/<ASIN> → HTTP 200，价格(priceValue/
  a-offscreen)、评分与评价元素都在同一页 → collect_detail 一次打开同步抽取
  价格 + 概要评分 + 评价（价格入 price_snapshot，评价入 review 表）。
- 结构与 amazon_ca 一致，抽取规则全在 schema.json（DOM 变了只改 schema）。
注意：单次探测通过不代表批量安全，规模化后大概率触发验证码，需低频 + 代理。
"""
from __future__ import annotations

import re
import urllib.parse

from ...models import (L3_STRONG, Product, PriceSnapshot, ProductPayload,
                       Review, ReviewSummary, SearchHit)
from .. import SiteAdapter
from ..amazon_common import parse_amazon_product as _parse_amazon_product


def _to_price(text: str) -> float | None:
    m = re.search(r"([\d,]+\.\d{2}|[\d,]+)", (text or "").replace(" ", " "))
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


class AmazonUsAdapter(SiteAdapter):
    code = "amazon_us"
    name = "Amazon US"
    base_url = "https://www.amazon.com"
    country = "USA"
    channel = "Amazon"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    login_probe_url = "https://www.amazon.com/gp/css/order-history"
    # ★ 商品页等待锚点：必须显式声明。
    # 采集链路用的是 `getattr(adapter, "retail_page_wait", "body")`，
    # 本类原先缺这个属性 → 兜底成 "body" → **页面骨架一出现就返回**，
    # 不等待 #productTitle/.a-price 渲染，造成抽取不稳定、偶发超时与 0 评价。
    # （注意：spec 的 fetch.retail_page_wait 目前**不被采集链路读取**，
    #   所以这里不写就等于没生效——amazon_ca/mx 均已声明。）
    retail_page_wait = "#productTitle, #dp, .a-price"
    suggested_interval = 8.0        # 强防护站点，宁慢不封

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

    def parse_search(self, dom, html: str = "", limit: int = 50) -> list[SearchHit]:
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for block in dom.sub("div[data-asin]:not([data-asin=''])", limit=limit * 3):
            asin = block.self_attr("data-asin") or ""
            if not asin or asin in seen:
                continue
            title = ""
            for sel in ("h2 span", "a[class*='s-line-clamp']",
                        "span.a-size-medium", "[data-cy='title-recipe'] span"):
                title = block.text(sel)
                if title:
                    break
            if not title:
                title = " ".join((block.self_text() or "").split()[:15])
            seen.add(asin)
            hits.append(SearchHit(sku=asin, url=self.product_url(asin),
                                  title=title, rank=len(hits) + 1))
            if len(hits) >= limit:
                break
        return hits

    # ---- 商品页：价格 + 评分 + 网评（共用实现，见 ..amazon_common）----
    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return _parse_amazon_product(
            dom, site_code=self.code, sku=sku, url=url,
            currency='USD', product_url='https://www.amazon.com/dp/{sku}')

