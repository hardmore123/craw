"""Costco（www.costco.ca / www.costco.com）共用的商品页增强解析。

为什么需要单独一层：
Costco 的商品标题写成 `Hisense 55" Class - U7SG Series - 4K QLED Mini LED TV`，
**里面没有完整型号**（不会出现 `55U7SG`）。JSON-LD 也只有 name/sku/offers/
aggregateRating，没有 mpn/model。所以：
  - 拿标题做「型号子串」匹配：正确商品会被拒（假阴性）；
  - 拿系列主干（U7SG）匹配：65 吋会撞上 75 吋（假阳性，张冠李戴）。
两条路都不可接受。实测 Costco 页面自己在两处标注了真实型号：
  1) 页头 `<div data-testid="Text_item-number">Item 8987855 | Model 55U78SG</div>`
  2) 规格表 `<th>Model</th><td>55U7SG</td>`（id=ProductSpecifications-row*）
两处可能不一致（Costco 会把系列型号与实际售卖型号都写出来），所以两个都收，
交给 `model_candidates` 做「精确相等」校验——任一命中即算命中，全部不中即拒。
"""
from __future__ import annotations

import re

from ..models import ProductPayload

# 页头：Item 8987855 | Model 55U78SG
_HEADER_MODEL_RE = re.compile(
    r"Text_item-number[^>]*>[^<]*?Model\s*([A-Za-z0-9][A-Za-z0-9._\-+]{1,39})", re.I)
# 规格表 Model 行（服务端渲染就有，纯 HTTP 也能命中）
_SPEC_MODEL_RE = re.compile(
    r">\s*Model\s*</th>\s*<td[^>]*>([^<]{1,40})</td>", re.I)
# BV 挂件上的商品号（=URL 里的 .product.<id>）
_BV_PID_RE = re.compile(r"data-bv-product-id=[\"'](\d+)[\"']", re.I)
# 尺寸：55" Class / 55 Class / 55-inch
_SIZE_RE = re.compile(r"\b(\d{2,3})\s*(?:\"|”|inch|in\b|-inch|\sclass\b)", re.I)


def _clean_model(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" \t|,;")
    # 规格表里偶尔带尾巴（"55U7SG (CA)"）；只保留型号本体
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._\-+]{1,39})", text)
    return m.group(1) if m else ""


def extract_models(html: str) -> list[str]:
    """按出现顺序返回去重后的权威型号码（页头 Model 优先）。

    防串味：商品页上可能还有「对比/相关商品」模块，如果它们复用了同款标记，
    会把别的型号收进来造成误命中。这里做两道收口：
      1) 页头只取**第一个** Model，规格表最多取 2 个；
      2) 若第一个候选带尺寸前缀（55U78SG → 55），其余候选必须同尺寸才保留
         —— 对比模块里的商品几乎不可能是同尺寸同系列，同尺寸约束能挡掉绝大部分。
    """
    raw: list[str] = []
    header = _HEADER_MODEL_RE.search(html or "")
    if header:
        model = _clean_model(header.group(1))
        if model:
            raw.append(model)
    for i, m in enumerate(_SPEC_MODEL_RE.finditer(html or "")):
        if i >= 2:
            break
        model = _clean_model(m.group(1))
        if model and model not in raw:
            raw.append(model)
    if not raw:
        return []
    lead = re.match(r"(\d{2,3})", raw[0])
    if not lead:
        return raw
    size = lead.group(1)
    return [m for m in raw if m.startswith(size)] or raw


def extract_bv_product_id(html: str = "", url: str = "") -> str:
    """BV 评价用的商品号：优先页面上的 data-bv-product-id，其次 URL 末段。"""
    m = _BV_PID_RE.search(html or "")
    if m:
        return m.group(1)
    m = re.search(r"\.product\.(\d+)\.html", url or "", re.I)
    return m.group(1) if m else ""


def extract_size(html: str = "", title: str = "") -> str:
    for source in (title, html or ""):
        m = _SIZE_RE.search(source or "")
        if m:
            return f'{m.group(1)}"'
    return ""


def enrich_costco_payload(payload: ProductPayload, html: str = "",
                          url: str = "") -> ProductPayload:
    """把 Costco 的权威型号/尺寸/BV 商品号补进 payload（不改价格与评价）。"""
    if payload is None:
        return payload
    product = payload.product
    models = extract_models(html)
    if models:
        # model 保留第一个（页头，Costco 自己标注的主型号）；
        # model_candidates 全量，供匹配做精确相等校验。
        product.model = product.model or models[0]
        existing = list(getattr(product, "model_candidates", None) or [])
        for m in models:
            if m not in existing:
                existing.append(m)
        product.model_candidates = existing
    if not product.size:
        product.size = extract_size(html, product.title)
    if not product.title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
        if m:
            product.title = re.sub(r"<[^>]+>", " ", m.group(1))
            product.title = " ".join(product.title.split())
    bv_id = extract_bv_product_id(html, url)
    if bv_id:
        product.bv_product_id = bv_id
    return payload
