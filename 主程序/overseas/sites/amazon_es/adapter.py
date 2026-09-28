"""Amazon Spain 适配器（价格 + 网评同页同步抓取）。

防护等级 L3（强）：必须真实浏览器，且要低频。实测（2026-09，cases.jsonl）：
- amazon_es 与 amazon_ca/us/mx/de/es/fr/it/uk 前端同构，data-hook 站内稳定属性，
  复用 overseas/sites/amazon_common.parse_amazon_product（含价格区校验，防配件价污染）。
- 搜索页 https://www.amazon.es/s?k=<kw> → HTTP 200，返回 div[data-asin] 候选
- 商品页 https://www.amazon.es/dp/<ASIN> → HTTP 200，价格/评分/评价在同一页
- 评价区懒加载：滚动+等待后首屏可抽 8~13 条（R 开头 Amazon review ID 作 review_key）；
  独立评价页 /product-reviews/<ASIN> 对代理/无会话返回空壳，PDP 内嵌才是可靠来源
- 货币：EUR；接入需输入邮编 28058（eu_sites_list.csv 标注）
注意：单次探测通过不代表批量安全，规模化后大概率触发验证码，需低频 + 代理。
"""
from __future__ import annotations

import urllib.parse

from ...models import L3_STRONG, ProductPayload, SearchHit
from .. import SiteAdapter
from ..amazon_common import parse_amazon_product as _parse_amazon_product


class AmazonEsAdapter(SiteAdapter):
    code = "amazon_es"
    name = "Amazon Spain"
    base_url = "https://www.amazon.es"
    country = "Spain"
    channel = "Amazon"
    protection = L3_STRONG
    requires_browser = True
    supports_retail = True
    retail_page_wait = "#productTitle, #dp, .a-price"
    suggested_interval = 8.0        # 强防护站点，宁慢不封
    login_probe_url = "https://www.amazon.es/gp/css/order-history"

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

    def parse_search(self, dom, html: str = "", limit: int = 40) -> list[SearchHit]:
        if dom is None:
            return []
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for block in dom.sub("div[data-asin]:not([data-asin=''])", limit=limit * 2):
            asin = (block.self_attr("data-asin") or "").strip()
            if not asin or asin in seen:
                continue
            seen.add(asin)
            title = ""
            for sel in ("h2 span:not([class])", "a[class*='s-line-clamp']",
                        "span.a-size-medium", "h2 span",
                        "[data-cy='title-recipe'] span"):
                title = block.text(sel)
                if title:
                    break
            if not title:
                title = " ".join((block.self_text() or "").split()[:15])
            hits.append(SearchHit(sku=asin, url=self.product_url(asin),
                                  title=title, rank=len(hits) + 1))
            if len(hits) >= limit:
                break
        return hits

    # ---- 商品页：价格 + 评分 + 网评（共用实现，见 ..amazon_common）----
    def parse_product(self, dom, sku: str = "", url: str = "") -> ProductPayload:
        return _parse_amazon_product(
            dom, site_code=self.code, sku=sku, url=url,
            currency='EUR', product_url='https://www.amazon.es/dp/{sku}')
