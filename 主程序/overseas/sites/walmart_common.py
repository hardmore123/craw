"""Walmart（www.walmart.com / www.walmart.ca）共用逻辑：取价护栏 + 权威型号。

为什么需要单独一层：

1) **取价护栏（防误抓的核心）**
   Walmart PDP 上除买盒主价外，还有「Similar items」「Customers also considered」
   「Sponsored」等轮播，其价格节点的 DOM 构造与主价高度相似。若无脑取页面上第一个
   价格元素，就会把配件/推荐商品价记成电视价——这正是 Amazon 32A4NV 事故的形态
   （页面无 buybox 主价时兜底选择器取到配件价 $9.49，且同页两次跑还漂移）。
   护栏做法与 `amazon_common.price_in_region` 保持一致：沿祖先链判断价格元素是否
   落在可信容器内，落在推荐位一律拒绝；**拿不到判定时保守拒绝**（宁可缺价，不可错价，
   错数据比缺数据危害大）。

2) **权威型号**
   Walmart 商品标题不一定含完整型号。JSON-LD 的 model/mpn 与规格表 Model 行才是
   权威来源，收进 `Product.model_candidates` 供 `_payload_matches_model` 做
   **精确相等**校验——有权威型号时不再退回宽松的标题/URL 子串匹配，
   避免 55 吋的价格被记到 75 吋头上（零售线红线）。

注意：本模块的价格兜底只在 **Playwright 浏览器路径**下有效（需要祖先链，
走 locator.evaluate）。HttpFetcher 路径下 `price_in_region` 恒为 False →
不产出兜底价。这是刻意的安全取向；主价仍由 JSON-LD `offers.price` 提供。
"""
from __future__ import annotations

import re

from ..models import ProductPayload

# 价格元素必须位于这些容器之一（按 id/class 片段匹配）
WALMART_PRICE_GOOD = (
    "price-wrap", "price-characteristic", "price-group", "list-price",
    "product-price", "buybox", "add-to-cart", "atom", "price-section",
)

# 祖先链里出现这些容器 → 明确是推荐位/轮播/搜索结果，直接拒绝
WALMART_PRICE_BAD = (
    "similar", "carousel", "recommend", "sponsored", "also-considered",
    "you-may-also", "customers-also", "shelf", "bundle", "comparison",
    "search-result", "item-stack", "grid-view", "tertiary",
)

PRICE_REGION_JS = """(e) => {
    const good = %s;
    const bad = %s;
    let p = e;
    for (let k = 0; k < 14 && p; k++) {
        const id = p.id || '';
        const cls = (p.className && typeof p.className === 'string') ? p.className : '';
        const ds = p.getAttribute ? (p.getAttribute('data-testid') || '') : '';
        const blob = (id + ' ' + cls + ' ' + ds).toLowerCase();
        if (blob) {
            if (bad.some(b => blob.includes(b))) return 'bad';
            if (good.some(g => blob.includes(g))) return 'ok';
        }
        p = p.parentElement;
    }
    return 'unknown';
}""" % (list(WALMART_PRICE_GOOD), list(WALMART_PRICE_BAD))

# 价格选择器优先链：带 itemprop/data-testid 的语义化节点优先，通用类名最后
PRICE_SELECTORS = (
    "[itemprop='price']",
    "[data-testid='price-wrap'] span[aria-hidden='true']",
    "[data-testid='price-wrap'] span",
    "span[data-automation-id='product-price']",
    "div[data-testid='list-price'] span",
    "div.price-characteristic-price span",
)


def price_in_region(dom, selector: str) -> bool:
    """selector 命中的第一个价格元素是否确实位于可信价格区内。

    需要祖先链，故走 Playwright 的 locator.evaluate（与 amazon_common 同款实现）。
    拿不到判定时**保守拒绝**。
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


def pick_price_text(dom) -> str:
    """按优先链取价格文本；每个候选都过价格区校验，全不通过则返回空串。

    返回空串表示"页面确实没有可信的主价格"（缺货/仅第三方在售/无买盒），
    调用方应据此留空价格，而不是退回随便一个价格。
    """
    for sel in PRICE_SELECTORS:
        try:
            cand = dom.text(sel)
        except Exception:
            cand = ""
        if not cand:
            continue
        if price_in_region(dom, sel):
            return cand
    return ""


# ---------------------------------------------------------------- 权威型号

# 规格表 Model 行：Walmart 规格区是 label/value 成对节点，标签与值之间可能有若干标签
_SPEC_MODEL_RE = re.compile(
    r">\s*(?:Model|Model Number|Item Model Number)\s*<[^>]*>\s*(?:<[^>]+>\s*)*"
    r"([A-Za-z0-9][A-Za-z0-9._\-+]{1,39})", re.I)
# JSON-LD 里的型号字段
_JSONLD_MODEL_RE = re.compile(
    r'"(?:mpn|model|sku)"\s*:\s*"([A-Za-z0-9][A-Za-z0-9._\-+ ]{1,39})"', re.I)
# 尺寸：55" / 55 inch / 55-inch / 55 Class / 55 in
_SIZE_RE = re.compile(r"\b(\d{2,3})\s*(?:\"|”|inch|in\b|-inch|\sclass\b)", re.I)


def _clean_model(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" \t|,;")
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._\-+]{1,39})", text)
    return m.group(1) if m else ""


def extract_models(html: str = "", fallback: str = "") -> list[str]:
    """按可靠度返回去重后的型号码：JSON-LD model/mpn > 规格表 Model 行。

    防串味收口（与 costco_common.extract_models 同一思路）：
      1) 规格表最多取 2 个，避免「对比商品」模块复用同款标记被收进来；
      2) 首个候选带尺寸前缀（55U7SG → 55）时，其余候选必须同尺寸才保留。
    """
    raw: list[str] = []

    for m in _JSONLD_MODEL_RE.finditer(html or ""):
        model = _clean_model(m.group(1))
        # sku 字段常是纯数字 item id，不是型号，跳过
        if model and not model.isdigit() and model not in raw:
            raw.append(model)

    for i, m in enumerate(_SPEC_MODEL_RE.finditer(html or "")):
        if i >= 2:
            break
        model = _clean_model(m.group(1))
        if model and not model.isdigit() and model not in raw:
            raw.append(model)

    if fallback:
        cleaned = _clean_model(fallback)
        if cleaned and cleaned not in raw:
            raw.insert(0, cleaned)

    if not raw:
        return []
    lead = re.match(r"(\d{2,3})", raw[0])
    if not lead:
        return raw
    size = lead.group(1)
    return [m for m in raw if m.startswith(size)] or raw


def extract_size(title: str = "", html: str = "") -> str:
    for source in (title, html or ""):
        m = _SIZE_RE.search(source or "")
        if m:
            return f'{m.group(1)}"'
    return ""


def enrich_walmart_payload(payload: ProductPayload, dom=None, html: str = "",
                           url: str = "") -> ProductPayload:
    """补权威型号/尺寸，并在 JSON-LD 无价时用**带护栏**的 CSS 兜底取价。

    不改 JSON-LD 已给出的价格与评价——那些是商品自身的权威数据。
    """
    if payload is None:
        return payload
    product = payload.product

    models = extract_models(html, fallback=product.model or "")
    if models:
        product.model = product.model or models[0]
        existing = list(getattr(product, "model_candidates", None) or [])
        for m in models:
            if m not in existing:
                existing.append(m)
        product.model_candidates = existing

    if not product.size:
        product.size = extract_size(product.title, html)

    # 取价兜底：仅在**没有可信价格**时尝试，且必须过价格区校验。
    # ★ 判空必须看到 price.price：spec 驱动抽取即使没抽到价格，也会产出一个
    #   price=None 的 PriceSnapshot，只判 `payload.price is None` 会让护栏永不触发。
    has_price = payload.price is not None and payload.price.price is not None
    if not has_price and dom is not None:
        from ..models import PriceSnapshot
        from ..retail_common import to_price
        price_text = pick_price_text(dom)
        price = to_price(price_text)
        if price is not None:
            payload.price = PriceSnapshot(price=price,
                                          currency="",
                                          raw_text=(price_text or "").strip())
    return payload
