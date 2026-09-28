"""零售站通用抽取工具：JSON-LD 商品/价格/评分 + 文本解析辅助。

多数加拿大零售站（Best Buy / Walmart / The Brick / Leon's / Visions /
Canadian Tire / Costco 等）在商品页嵌 <script type="application/ld+json">，
含 Product / Offer(price) / AggregateRating(ratingValue,reviewCount)，以及可能的
Review 列表。相比各站混淆的 CSS 类名，JSON-LD 更稳定、跨站通用。

零售适配器的 parse_product 可直接调用 jsonld_product_payload(dom, ...) 兜底，
再按需用站点专有 CSS 选择器补充。同一商品页同步产出价格 + 评分 + 评价。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .models import (Product, PriceSnapshot, ProductPayload, Review,
                     ReviewSummary, SearchHit)


def search_link_hits(dom, html: str, selector: str, pattern,
                     base_url: str, limit: int = 40, sku_group: int = 1,
                     skip_skus: set[str] | None = None) -> list[SearchHit]:
    """从零售搜索结果链接提取候选，并保留链接标题供型号匹配。

    纯 SKU/slug 不能证明搜索结果就是目标型号；这里先保留卡片链接上的
    aria-label/title/可见文本，详情页仍需由调用方做最终型号校验。
    """
    regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
    skip = {str(value).lower() for value in (skip_skus or set())}
    hits: list[SearchHit] = []
    seen: set[str] = set()

    def add(href: str, title: str = "") -> None:
        if len(hits) >= limit:
            return
        match = regex.search(href or "")
        if not match:
            return
        try:
            sku = str(match.group(sku_group) or "").strip()
        except (IndexError, AttributeError):
            return
        if not sku or sku.lower() in skip or sku in seen:
            return
        seen.add(sku)
        url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
        hits.append(SearchHit(sku=sku, url=url.split("?")[0],
                              title=(title or "").strip(), rank=len(hits) + 1))

    if dom is not None:
        try:
            for link in dom.sub(selector, limit=max(limit * 3, limit)):
                href = link.self_attr("href")
                if not href:
                    continue
                title = (link.self_attr("aria-label") or
                         link.self_attr("title") or
                         link.self_attr("data-product-name") or
                         link.self_attr("data-product-title") or
                         link.self_text())
                add(href, title)
        except Exception:
            pass

    # 浏览器 DOM 是主路径；保留 HTML 回退，兼容静态 mock/部分页面结构。
    for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', html or "", re.I):
        add(href)
        if len(hits) >= limit:
            break
    return hits


def to_price(text: Any) -> float | None:
    if text is None:
        return None
    m = re.search(r"([\d,]+\.\d{2}|[\d,]+)", str(text).replace("\u00a0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def to_float(text: Any) -> float | None:
    if text is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(text))
    return float(m.group(1)) if m else None


def to_int(text: Any) -> int | None:
    if text is None:
        return None
    m = re.search(r"([\d,]+)", str(text))
    return int(m.group(1).replace(",", "")) if m else None


def jsonld_blocks(html: str) -> list[dict]:
    """从 HTML 抽取所有 JSON-LD 块，展开 @graph，返回 dict 列表。"""
    out: list[dict] = []
    for raw in re.findall(
            r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>',
            html or "", re.S | re.I):
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            # 有的站点把多个对象拼在一起或有尾随逗号，尽力容错
            try:
                data = json.loads(raw.replace("\n", " "))
            except Exception:
                continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if "@graph" in item and isinstance(item["@graph"], list):
                out.extend(g for g in item["@graph"] if isinstance(g, dict))
            else:
                out.append(item)
    return out


def _type_matches(node: dict, wanted: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == wanted.lower() for x in t)
    return str(t or "").lower() == wanted.lower()


def find_product_node(blocks: list[dict]) -> dict | None:
    for b in blocks:
        if _type_matches(b, "Product") or _type_matches(b, "ProductGroup"):
            return b
    return None


def _offer_price(node: dict) -> tuple[float | None, str, float | None]:
    """从 Product 节点的 offers 取 (price, currency, listPrice)。"""
    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if not isinstance(offers, dict):
        return None, "", None
    price = to_price(offers.get("price") or offers.get("lowPrice"))
    currency = str(offers.get("priceCurrency") or "")
    list_price = to_price(offers.get("highPrice"))
    return price, currency, list_price


def jsonld_product_payload(dom, sku: str = "", url: str = "",
                           site_code: str = "", channel: str = "",
                           default_currency: str = "CAD") -> ProductPayload:
    """用 JSON-LD 从商品页同步抽取价格 + 评分聚合 + 评价，返回 ProductPayload。

    dom 需能取整页 HTML（dom.html()）。抓不到 JSON-LD Product 时返回骨架 payload。
    """
    product = Product(site_code=site_code, sku=sku, url=url, category="TV")
    payload = ProductPayload(product=product)
    if dom is None:
        return payload
    try:
        html = dom.html()
    except Exception:
        html = ""
    blocks = jsonld_blocks(html)
    node = find_product_node(blocks)
    if node is None:
        # 有些站点只把商品结构放进前端状态树，JSON-LD 不稳定；详情页 h1
        # 仍可作为型号权威校验依据，但不能凭它生成价格/评价数据。
        try:
            product.title = str(dom.text("h1") or "").strip()
        except Exception:
            product.title = ""
        return payload

    product.title = str(node.get("name") or "").strip()
    product.brand = _brand_name(node.get("brand"))
    mpn = node.get("mpn") or node.get("sku") or node.get("model")
    if mpn:
        product.model = str(mpn).strip()

    price, currency, list_price = _offer_price(node)
    if price is None and _type_matches(node, "ProductGroup"):
        # ProductGroup：取变体里第一个有价的
        for v in node.get("hasVariant") or []:
            if isinstance(v, dict):
                price, currency, list_price = _offer_price(v)
                if price is not None:
                    break
    if price is not None:
        payload.price = PriceSnapshot(price=price, list_price=list_price,
                                      currency=currency or default_currency,
                                      raw_text=str(price))

    agg = node.get("aggregateRating")
    if isinstance(agg, dict):
        payload.summary = ReviewSummary(
            avg_rating=to_float(agg.get("ratingValue")),
            total_count=to_int(agg.get("reviewCount") or agg.get("ratingCount")))

    reviews: list[Review] = []
    raw_reviews = node.get("review")
    if isinstance(raw_reviews, dict):
        raw_reviews = [raw_reviews]
    for r in (raw_reviews or []):
        if not isinstance(r, dict):
            continue
        rating = None
        rr = r.get("reviewRating")
        if isinstance(rr, dict):
            rating = to_float(rr.get("ratingValue"))
        author = r.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        body = str(r.get("reviewBody") or r.get("description") or "").strip()
        title = str(r.get("name") or r.get("headline") or "").strip()
        if not (body or title):
            continue
        reviews.append(Review(
            review_key=str(r.get("@id") or (title[:40] + str(r.get("datePublished") or ""))),
            rating=rating, title=title, body=body,
            author=str(author or ""), review_date=str(r.get("datePublished") or ""),
            review_url=url))
    payload.reviews = reviews
    return payload


def _brand_name(brand: Any) -> str:
    if isinstance(brand, dict):
        return str(brand.get("name") or "").strip()
    return str(brand or "").strip()
