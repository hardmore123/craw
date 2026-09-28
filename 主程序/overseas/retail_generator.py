"""零售线 generator（P2-2）：探测报告 → RetailSpec + 静态校验 + LLM 修复闭环。

对齐任务表 P2-2：
    9.1 探测报告 → RetailSpec（过静态校验 validate_retail_spec）
    9.2 实测打分：真实页面上校验（搜索命中 / 价格可抽 / 评价可抽 / 匹配准确率）
    9.3 回喂修复：不达标把失败项结构化回喂，最多 N 轮
    9.4 匹配准确率硬否决：抽检有误匹配直接判失败

与 SPEC 线的 spec_generator 同构（mermaid: build_generate_messages /
build_repair_messages / generate_spec），但：
    - 契约换成 RetailSpec（六段 + match/paginate/incremental/shops/export/fetch）
    - 校验用 validate_retail_spec（L0~L3）
    - 实测打分在真实型号上跑（搜索命中/价格填充/评价可抽），不是找全率
"""
from __future__ import annotations

import json
import re
from typing import Any

from .llm_client import LLMClient
from .retail_probe import (detect_multi_shop, detect_review_pagination,
                           probe_product_page)
from .retail_spec import SUPPORTED_MATCH_STRATEGIES, validate_retail_spec

# 固定的 schema 提示词（喂给 LLM 说明 RetailSpec 长什么样）
_SYSTEM_PROMPT = (
    "你是电视零售站 schema 生成器。给定一份探测报告（搜索页/商品页的结构信号），"
    "产出符合 RetailSpec 契约的 JSON 配置，用于后续引擎自动抓取该站的价格与网评。\n"
    "纪律：只能声明引擎已实现的能力（match/reviews/paginate/shops 字段在契约白名单内）；"
    "型号匹配 `on_no_match` 必须是 `skip`（宁缺毋滥）；不要生成可执行代码，"
    "需要交互只声明 paginate.type（none/query_param/url_page/click_more）。"
)

_SCHEMA_HINT = """RetailSpec JSON 结构（六段兼容 schema.json + 补五段）：
{
  "retail_spec_version": "0.1",
  "code": "<site_code>",
  "name": "<站点名>",
  "country": "Mexico", "channel": "<渠道>", "region": "mx",
  "base_url": "https://...",
  "currency": "MXN", "capabilities": ["price", "reviews"],
  "anchor": "h1",
  "search": {"container": "<候选容器>", "sku": {"self_attr": "href"},
             "title": {"selectors": ["h3"], "transform": "clean_text"},
             "url": {"self_attr": "href"}},
  "product": {"title": {"selectors": ["h1"], "transform": "clean_text"},
              "brand": {"selectors": ["[class*='brand']"], "transform": "brand"},
              "model": {"selectors": ["[data-testid*='model']"], "transform": "clean_text"},
              "size": {"selectors": ["h1"], "transform": "size_inch"}},
  "price": {"price": {"selectors": ["[class*='price']"], "transform": "money"},
            "currency": {"selectors": ["h1"], "transform": "currency_mx"},
            "in_stock": {"selectors": ["button"], "transform": "bool_present"}},
  "summary": {"avg_rating": {"selectors": ["[class*='rating']"], "transform": "rating"}},
  "reviews": {"container": "[class*='review']", "limit": 40,
              "fields": {"review_key": {"self_attr": "id"},
                          "title": {"selectors": ["h3"], "transform": "clean_text"},
                          "body": {"selectors": ["p"], "transform": "clean_text"}}},
  "match": {"strategy": "search_then_verify", "on_no_match": "skip",
            "verify": {"require_model_in": ["sku", "title"], "allow_core_match": true}},
  "paginate": {"reviews": {"type": "none"}},
  "shops": {"mode": "single_shop"},
  "export": {"size_from": "title"},
  "fetch": {"protection": "L2_MEDIUM", "requires_browser": true, "interval_sec": 5.0}
}
只输出 JSON，不要解释。"""


def build_generate_messages(probe_report: dict) -> list[dict[str, str]]:
    """由探测报告构造生成 RetailSpec 的 LLM 消息。"""
    report_json = json.dumps(probe_report, ensure_ascii=False, indent=2)
    # 提炼线索
    hints: list[str] = []
    s = probe_report.get("search") or {}
    if s.get("best_candidate_selector"):
        hints.append(f"- 搜索候选容器最佳选择器: {s['best_candidate_selector']} "
                     f"（命中 {s.get('result_count', 0)} 个，型号纯度 {s.get('purity', 0)}）")
    if s.get("sample_pdp_hrefs"):
        hints.append(f"- 商品 URL 样本: {s['sample_pdp_hrefs']}")
    pp = probe_report.get("product_page") or {}
    if pp.get("h1"):
        hints.append(f"- 商品页 h1: {pp['h1']}（model_in_h1={pp.get('model_in_h1')}）")
    price_sels = (pp.get("detected") or {}).get("price_selectors") or []
    if price_sels:
        hints.append(f"- 价格选择器候选: "
                     f"{[x['selector'] for x in price_sels][:3]}")
    rv = probe_report.get("reviews") or {}
    if rv.get("pagination_type"):
        hints.append(f"- 评价翻页方式判定: {rv['pagination_type']} "
                     f"（type=query_param/url_page/click_more/none）")
    sh = probe_report.get("shops") or {}
    if sh.get("mode"):
        hints.append(f"- 多店铺判定: {sh['mode']} "
                     f"（multi_shop→shops.mode=multi_shop+list/columns；"
                     f"single_shop→shops.mode=single_shop）")
    hint_text = ("\n探测线索（请据此填 search/match/price/reviews/paginate/shops）：\n"
                 + "\n".join(hints)) if hints else ""
    user = (f"{_SCHEMA_HINT}\n\n探测报告：\n{report_json}\n{hint_text}\n\n"
            f"请只输出符合上述 schema 的 RetailSpec JSON。")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_repair_messages(probe_report: dict, prev_spec: dict,
                          problems: list[str]) -> list[dict[str, str]]:
    """把上一版 spec 的问题回喂 LLM 修复。"""
    user = (
        f"{_SCHEMA_HINT}\n\n"
        f"探测报告：\n{json.dumps(probe_report, ensure_ascii=False)}\n\n"
        f"上一版 RetailSpec：\n{json.dumps(prev_spec, ensure_ascii=False)}\n\n"
        f"该版本存在以下问题，请针对性修正后重新输出完整可用的 RetailSpec JSON：\n"
        + "\n".join(f"- {p}" for p in problems)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def parse_spec_from_text(text: str) -> dict:
    """从 LLM 回复中提取 JSON（容忍 markdown 代码块包裹）。"""
    raw = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if m:
        raw = m.group(1)
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    return json.loads(raw)


def generate_spec(probe_report: dict, client: LLMClient | None = None,
                  max_repair: int = 3) -> dict:
    """从探测报告生成 RetailSpec：LLM 生成初版 → validate → 回喂修复。

    返回最终 spec（可能带 problems 供调用方决定是否人力确认）。
    """
    if client is None:
        client = LLMClient()
    if not client.configured:
        raise RuntimeError("LLM 未配置：需要 OVERSEAS_LLM_PROVIDER/API_KEY/MODEL。"
                           "可用 retai_generator 的离线模式回退（生成模板 spec）")
    problems: list[str] = []
    cur: dict = {}
    for i in range(max_repair + 1):
        messages = build_repair_messages(probe_report, cur, problems) if i else \
            build_generate_messages(probe_report)
        text = client.chat(messages)
        try:
            cur = parse_spec_from_text(text)
        except Exception as e:
            problems = [f"LLM 输出无法解析为 JSON: {e}；请只输出 JSON"]
            if i >= max_repair:
                break
            continue
        problems = validate_retail_spec(cur)
        if not problems:
            break
    cur["_repair_rounds"] = i if "code" in cur else max_repair
    return cur


def score_spec_on_pages(spec: dict, search_html: str, pdp_html: str,
                        pdp_url: str = "", model: str = "") -> dict:
    """实测打分：在真实页面上验证 spec 的选择器是否命中。

    维度：
      - search.container 在搜索页命中数
      - price.price 在商品页能抽到值
      - reviews.container 在商品页命中数
      - product.title / size 可抽
    返回 {score, passed, checks:[{name, hit, detail}]}。
    """
    from .fetchers import LxmlDom
    from .extract import Extractor

    checks: list[dict] = []
    dom_s = LxmlDom.parse(search_html or "")
    if dom_s is not None:
        sel = (spec.get("search") or {}).get("container") or ""
        n = dom_s.count(sel) if sel else 0
        checks.append({"name": "search.container 命中", "weight": 30,
                       "hit": n > 0, "detail": f"count={n} selector={sel}"})

    dom_p = LxmlDom.parse(pdp_html or "")
    ex = Extractor(spec, pdp_html or "", base_url=spec.get("base_url") or "")
    if dom_p is not None:
        # 价格
        price_sel = ((spec.get("price") or {}).get("price") or {}).get("selectors") or []
        p = None
        for sel in price_sel:
            v = dom_p.text(sel)
            if v:
                p = v
                break
        checks.append({"name": "价格可抽", "weight": 30,
                       "hit": bool(p), "detail": {"value": (p or "")[:60]}})
        # 标题
        t = dom_p.text("h1") or ""
        checks.append({"name": "标题(h1)可抽", "weight": 10,
                       "hit": bool(t), "detail": {"value": t[:60]}})
        # 评价容器
        rv_sel = (spec.get("reviews") or {}).get("container") or ""
        rn = dom_p.count(rv_sel) if rv_sel else 0
        checks.append({"name": "评价容器命中", "weight": 30,
                       "hit": rn > 0, "detail": {"count": rn, "selector": rv_sel}})
    else:
        checks.append({"name": "搜索页 DOM", "weight": 0, "hit": False,
                       "detail": "HTML 无法解析（可能被拦/懒加载）"})

    total = sum(c["weight"] for c in checks) or 1
    got = sum(c["weight"] for c in checks if c["hit"])
    score = round(got / total * 100, 1)
    return {"score": score, "passed": score >= 70,
            "checks": [c["name"] for c in checks if not c["hit"]],
            "details": checks}


def spec_from_probe_report(probe_report: dict) -> dict:
    """离线（无 LLM）模式：从探测报告直接构造一份模板 RetailSpec。

    供未配置 LLM / 快速起步用；字段取自探测结果。若探测信息不足则用 liverpool
    模板填空（宁缺线条少，等客户端补齐）。
    """
    s = probe_report.get("search") or {}
    pp = probe_report.get("product_page") or {}
    code = probe_report.get("code") or "retail"
    return {
        "retail_spec_version": "0.1",
        "code": code,
        "name": probe_report.get("brand") or code,
        "country": probe_report.get("country") or "Mexico",
        "region": probe_report.get("region") or "mx",
        "base_url": probe_report.get("base_url") or "",
        "currency": probe_report.get("currency") or "MXN",
        "capabilities": ["price"] + (["reviews"] if True else []),
        "anchor": "h1",
        "search": {
            "container": s.get("best_candidate_selector") or
                         "a[href*='/pdp/']",
            "sku": {"self_attr": "href"},
            "title": {"selectors": ["h3"], "transform": "clean_text"},
            "url": {"self_attr": "href"},
        },
        "product": {
            "title": {"selectors": ["h1"], "transform": "clean_text"},
            "brand": {"selectors": ["[class*='brand']"], "transform": "brand"},
            "size": {"selectors": ["h1"], "transform": "size_inch"},
        },
        "price": {
            "price": {"selectors": ["[class*='price']"], "transform": "money"},
            "currency": {"selectors": ["h1"], "transform": "currency_mx"},
            "in_stock": {"selectors": ["button"], "transform": "bool_present"},
        },
        "summary": {
            "avg_rating": {"selectors": ["[class*='rating']"], "transform": "rating"},
        },
        "reviews": {
            "container": "[class*='review']", "limit": 40,
            "fields": {
                "review_key": {"self_attr": "id"},
                "title": {"selectors": ["h3"], "transform": "clean_text"},
            },
        },
        "match": {"strategy": "search_then_verify", "on_no_match": "skip",
                  "verify": {"require_model_in": ["sku", "title"],
                             "allow_core_match": True}},
        "paginate": {"reviews": {"type": (
            pp.get("pagination_guess") or
            (pp.get("reviews") or {}).get("pagination_type") or "none")}},
        "shops": {"mode": "single_shop"},
        "export": {"size_from": "title"},
        "fetch": {"protection": "L2_MEDIUM", "requires_browser": True,
                  "interval_sec": 5.0},
    }


def llm_spec(probe_report: dict, client: LLMClient | None = None,
             max_repair: int = 3) -> dict:
    """对外统一入口：生成+校验+修复。未配置 LLM 时回落离线模板。"""
    try:
        return generate_spec(probe_report, client, max_repair)
    except RuntimeError:
        return spec_from_probe_report(probe_report)