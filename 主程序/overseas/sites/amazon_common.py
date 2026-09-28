"""Amazon 各站（ca/us/mx）共用逻辑。

目前只有一项：**价格区校验**——防止把推荐位/配件轮播的价格当成电视价。
三个站前端同构，这段逻辑必须共用，避免各站实现漂移。

背景（2026-09-20 实测事故）：
    Hisense 32A4NV（ASIN B0F34L9MZB）页面**没有 buybox 主价格**
    （#buybox 显示 "See All Buying Options"），但页面上有 18 个 .a-price
    元素，全部来自「Products related to this item」配件轮播（TV 保护套/遥控器）
    与推广轮播。兜底选择器 `span.a-price span.a-offscreen` 取到第一个匹配，
    于是把配件价当成了电视价（$9.49；同页两次跑分别是 $9.59/$10.25，会漂移）。
"""
from __future__ import annotations

# 价格元素必须位于这些容器之一（按 id 或 class 片段匹配）
PRICE_GOOD = (
    "corePrice_feature_div", "corePriceDisplay_desktop_feature_div",
    "apex_desktop", "desktop_buybox", "buybox", "price_inside_buybox",
    "sns-base-price", "newBuyBoxPrice", "usedBuySection",
    "all-offers-display", "aod-price", "aod-offer",
)

# 祖先链里出现这些容器 → 明确是推荐位/轮播，直接拒绝
PRICE_BAD = (
    "_c2Itb_", "a-carousel", "p13n", "similarities", "sp_detail",
    "cr-product-insights", "ask-btf", "va-card", "octopus",
)

# 老模板专属 id：本身即价格区，无需再校验祖先
LEGACY_PRICE_IDS = ("#priceblock_ourprice", "#priceblock_dealprice")

PRICE_REGION_JS = """(e) => {
    const good = %s;
    const bad = %s;
    let p = e;
    for (let k = 0; k < 14 && p; k++) {
        const id = p.id || '';
        const cls = (p.className && typeof p.className === 'string') ? p.className : '';
        if (id) {
            if (bad.some(b => id.includes(b))) return 'bad';
            if (good.includes(id)) return 'ok';
        }
        if (cls) {
            if (bad.some(b => cls.includes(b))) return 'bad';
            for (const g of good) { if (cls.includes(g)) return 'ok'; }
        }
        p = p.parentElement;
    }
    return 'unknown';
}""" % (list(PRICE_GOOD), list(PRICE_BAD))


def price_in_region(dom, selector: str) -> bool:
    """selector 命中的第一个价格元素是否确实位于核心价格区/买盒内。

    需要祖先链，故走 Playwright 的 locator.evaluate。拿不到判定时**保守拒绝**：
    宁可判为无价，也不把配件价当电视价（错数据比缺数据危害大）。
    """
    try:
        root = getattr(dom, "_root", None)
        if root is None or not hasattr(root, "locator"):
            return False
        loc = root.locator(selector)
        if loc.count() == 0:
            return False
        return bool(loc.first.evaluate(PRICE_REGION_JS) == "ok")
    except Exception:
        return False


# 三个站共用的价格选择器优先链
PRICE_SELECTORS = (
    "#corePrice_feature_div .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#corePrice_feature_div span.a-price span.a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.a-price span.a-offscreen",
    ".a-price .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
)


def pick_price_text(dom) -> str:
    """按优先链取价格文本；每个候选都过价格区校验，全不通过则返回空串。

    返回空串表示"页面确实没有可信的主价格"（缺货/仅第三方在售），
    调用方应据此留空价格，而不是退回随便一个价格。
    """
    for sel in PRICE_SELECTORS:
        cand = ""
        try:
            cand = dom.text(sel)
        except Exception:
            cand = ""
        if not cand:
            continue
        if sel in LEGACY_PRICE_IDS:
            return cand
        if price_in_region(dom, sel):
            return cand
    return ""


# ---------------------------------------------------------------- 数值解析

def to_price(text: str):
    """'$1,299.99' → 1299.99；取不到返回 None。"""
    import re
    m = re.search(r"([\d,]+\.\d{2}|[\d,]+)", (text or "").replace("\u00a0", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def to_float(text: str):
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def to_int(text: str):
    import re
    m = re.search(r"([\d,]+)", text or "")
    return int(m.group(1).replace(",", "")) if m else None


# ---------------------------------------------------------------- 商品页抽取

def parse_amazon_product(dom, *, site_code: str, sku: str = "", url: str = "",
                         currency: str = "", product_url: str = ""):
    """Amazon ca/us/mx 共用的商品页抽取（价格 + 评分 + 网评）。

    三站前端同构，共用一份可避免"某一站忘了修选择器"这类漂移——历史上
    amazon_ca/us 修了 reviewTitle/reviewText，而 mx 因走 schema 路径未被覆盖。
    """
    from overseas.models import (Product, ProductPayload, PriceSnapshot,
                                 Review, ReviewSummary)

    product = Product(site_code=site_code, sku=sku,
                      url=url or (product_url.format(sku=sku) if sku else ""),
                      brand="", category="TV")
    payload = ProductPayload(product=product)
    if dom is None:
        return payload

    product.title = dom.text("#productTitle") or dom.text("span#productTitle")

    # 尺寸兜底：规格/变体选择器常因未展开而取不到，而标题必带尺寸
    try:
        from overseas.extract import t_size_inch
        product.size = t_size_inch(product.title)
    except Exception:
        pass

    # 价格（含价格区校验，见模块 docstring 的 32A4NV 事故）
    price_text = pick_price_text(dom)
    price = to_price(price_text)
    list_text = (dom.text(".basisPrice .a-offscreen")
                 or dom.text("span.a-text-price .a-offscreen"))
    if price is not None:
        payload.price = PriceSnapshot(price=price,
                                      list_price=to_price(list_text),
                                      currency=currency or "",
                                      raw_text=(price_text or "").strip())

    # 评分聚合
    avg = to_float(dom.text("#acrPopover [data-hook='rating-out-of-text']")
                   or dom.text("[data-hook='rating-out-of-text']")
                   or dom.attr("#acrPopover", "title"))
    total = to_int(dom.text("#acrCustomerReviewText")
                   or dom.text("[data-hook='total-review-count']"))
    if avg is not None or total is not None:
        payload.summary = ReviewSummary(avg_rating=avg, total_count=total)

    # 「Customers say」平台摘要（data-testid 稳定，非哈希类名）
    say = dom.text("[data-testid='overall-summary']")
    if say:
        if payload.summary is None:
            payload.summary = ReviewSummary()
        payload.summary.summary_text = say[:1000]

    # 商品页内嵌网评。★ 新模板用驼峰 reviewTitle / reviewText；
    # 旧选择器 review-title / review-body 在新 DOM 上恒为空（会静默返回 0 条）。
    reviews = []
    for block in dom.sub("[data-hook='review']", limit=20):
        rid = (block.self_attr("id") or "").strip()
        rating = to_float(block.text("[data-hook='review-star-rating']")
                          or block.text("[data-hook='cmps-review-star-rating']"))
        title = (block.text("[data-hook='reviewTitle']")
                 or block.text("[data-hook='review-title']"))
        body = (block.text("[data-hook='reviewText']")
                or block.text("[data-hook='reviewTextContainer']")
                or block.text("[data-hook='review-body']"))
        author = block.text(".a-profile-name")
        date = block.text("[data-hook='review-date']")
        helpful = to_int(block.text("[data-hook='helpful-vote-statement']"))
        verified = bool(block.text("[data-hook='avp-badge']"))
        if not (title or body):
            continue
        reviews.append(Review(
            review_key=rid or (title[:40] + date),
            rating=rating, title=title, body=body, author=author,
            review_date=date, verified=verified, helpful_count=helpful,
            review_url=url or product.url))
    payload.reviews = reviews
    return payload
