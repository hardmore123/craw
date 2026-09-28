"""零售线探测器（P2-1）：探搜索页 / 商品页 / 评价页 / 多店铺，输出结构化报告。

对齐任务表 P2-1：
    8.1 探搜索页  用已知型号搜索，排名候选容器选择器（按「含型号比例」纯度排序）
    8.2 探商品页  定位价格区/评分区/规格区候选
    8.3 探评价页  评价是否独立页、翻页方式（url_page/query_param/click_more/none）
    8.4 探多店铺  检测商品页是否存在多家店铺报价表
    8.5 输出探测报告（结构化 JSON，字段够 Generate 用）

探测是「判断」不是「执行」：结果喂给 retail_generator（智能体/LLM）产出 RetailSpec，
之后引擎按 spec 硬编码执行，探测器只在接新站 / 改版时跑一次。

用法：
    from overseas.retail_probe import probe_retail_site
    report, best_html = probe_retail_site(fetcher, "coppel_mx", "75U6SV",
                                          base_url="https://www.coppel.com",
                                          brand="Hisense")
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .fetchers import LxmlDom
from .retail_compliance import ensure_allowed


def _norm_model(s: str) -> str:
    """型号归一化：去空格/连字符/下划线并大写。"""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def build_retail_report(*, code: str, brand: str = "", model: str = "",
                        base_url: str = "", search_kw: str = "",
                        note: str = "", search_url: str = "") -> dict:
    """构造探测报告（搜索失败/异常时用与成功探测一致的结构返回）。

    probe 的调用方（skill probe.py / retail_generator）只认这一份报告的字段形状；
    搜索打不开/被拦/异常时也必须返回同构 JSON + note 说明原因，而不是抛异常，
    这样才能把「站点不可达/反爬」如实汇报出来（任务表 R1-10 反爬分类汇报）。
    """
    return {
        "code": code,
        "brand": brand or "",
        "model": model or "",
        "base_url": base_url or "",
        "search_kw": search_kw or "",
        "search_url": search_url or "",
        "note": note or "",
        "search": {
            "best_candidate_selector": "",
            "candidate_ranking": [],
            "model_hit": 0,
            "purity": 0.0,
            "result_count": 0,
            "sample_pdp_hrefs": [],
        },
        "product_page": {"note": note or "未探测（搜索异常/被拦）"},
        "reviews": {"pagination_type": "none"},
        "shops": {"mode": "single_shop", "signal": ""},
    }


def _rank_on_dom(dom: LxmlDom | None, candidates: list[str],
                 want: str) -> list[dict]:
    """在搜索页 DOM 上排名候选容器选择器，按「含型号纯度」降序。

    返回 [{selector, count, model_hit, purity, sample}]。
    """
    if dom is None:
        return []
    ranked: list[dict] = []
    for sel in candidates:
        try:
            nodes = dom.sub(sel, limit=300)
        except Exception:
            continue
        if not nodes:
            continue
        total = len(nodes)
        hit = 0
        samples: list[str] = []
        for n in nodes:
            text = (n.self_text() or "")[:160]
            href = (n.self_attr("href") or "")[:120]
            if want and want in _norm_model(text + " " + href):
                hit += 1
                if len(samples) < 5:
                    samples.append((text or href or "")[:80])
        purity = hit / total if total else 0.0
        ranked.append({"selector": sel, "count": total, "model_hit": hit,
                       "purity": round(purity, 3), "sample": samples})
    return sorted(ranked, key=lambda r: (-r["purity"], -r["count"]))


# 常见搜索结果容器候选（按站型组织，探测时逐批尝试）
_SEARCH_CONTAINER_CANDIDATES = [
    "a[href*='/pdp/']",            # liverpool / coppel 类
    "a[href*='/producto/']",       # liverpool / Walmart 西语
    "a[href*='/product/']",        # 通用
    "a[href*='/dp/']",             # Amazon 系
    "[class*='card'] a[href]",     # 卡片通用
    "li a[href]",                  # 兜底（纯度排序会把它压后）
]


def _default_candidates() -> list[str]:
    return list(_SEARCH_CONTAINER_CANDIDATES)


def detect_review_pagination(html: str, *, has_review_block: bool = True) -> str:
    """从商品页 HTML 判评价翻页方式：url_page / query_param / click_more / none。

    R1-11 加强（对齐交接文档 S9）：
      - 补 `rel=next` 链接识别（标准翻页信号，Amazon/BestBuy 等广泛使用）
      - 补 `aria-label=页面N/next/siguiente` 按钮识别
      - 补图标按钮常见 class（pagination-next / pager__next / next-btn 等）
      - 补 Amazon 系评价特征（cm_cr_arp / loadFarenheit / pageNumber=）
      - 修死分支：has_review_block 现在真正参与判定（无评价块且无翻页信号 → none）
    返回仍是契约字符串（query_param / url_page / click_more / none），调用方据此回填
    spec 的 paginate.reviews.type。需要参数名等更多细节时用
    detect_review_pagination_ex（返回含 hint/candidates 的 dict）。
    """
    near = detect_review_pagination_ex(html, has_review_block=has_review_block)
    return str(near.get("type") or "none")


def detect_review_pagination_ex(html: str, *,
                                has_review_block: bool = True) -> dict:
    """增强版评价翻页探测：返回 {type, param, url_template, hint} 供回填 spec。

    判定优先级（重要信号优先）：
      1. Amazon 式 pageNumber / cm_cr_（独立评论页 query_param）
      2. 查询串页码 ?page= / ?pageNumber= / ?pg=   -> query_param（带 param 名）
      3. 路径页码 /reviews/xxx/page/2             -> url_page
      4. rel=next 链接（+ 有评价块）               -> query_param（param=page 待看链接）
      5. aria-label（Next/Siguiente/下一页）+ 评价块 -> query_param（param=page）
      6. 加载更多(View More/Load More/Ver Mas) + 评价块 -> click_more
      7. 其余                                    -> none

    返回 dict 供调用方直接回填 spec.paginate.reviews；detect_review_pagination
    只取 .type 字符串兼容老契约。
    """
    low = (html or "").lower()
    hints: list[str] = []

    # 1) Amazon 式评论：pageNumber= / cm_cr_ / review-accordion
    amz = re.search(r'pageNumber\s*=\s*\d+', low) or re.search(
        r'cm_cr_arp_dp|cm_cr_odp_dp|review-accordion', low)
    if amz:
        hints.append('Amazon-style 独立评论翻页 (pageNumber)')

    # 2) 查询串页码参数，如 ?page=2 / &pageNumber=3 / ?pg=1
    m_param = re.search(r'[?&](page\w*|pg|p)\s*=\s*\d+', low)
    if m_param:
        hints.append('查询串页码参数 ' + m_param.group(1))

    # 3) 路径页码，如 /reviews/xxx/2/ 或 /opiniones/xxx/pagina/2
    m_path = re.search(
        r'/(?:reviews?|opiniones|resenas|p[aá]gina)[^\s<>"\']*'
        r'(?:page|p[aá]gina)?[\/\-=]?\d+', low)

    # 4) rel=next（标准翻页链接）
    rel_next = bool(re.search(r'rel\s*=\s*["\']next["\']', low, re.I))
    if rel_next:
        hints.append('rel=next 链接')

    # 5) aria-label 翻页按钮（Next / Siguiente / 第N页）
    aria = re.search(
        r'aria-label=["\']([^"\']*(?:next|siguiente|p[aá]gina|page)[^"\']*)["\']',
        low, re.I)
    if aria:
        hints.append('aria-label 翻页按钮: ' + aria.group(1)[:50])

    # 6) 加载更多按钮（View More / Load More / Ver Mas / Mostrar Mas）
    more = re.search(r'(ver m[aá]s|mostrar m[aá]s|ver todas las|ver todos|'
                     r'load more|show more|view more|see more|read more)', low, re.I)
    if more:
        hints.append('加载更多按钮: ' + more.group(1))

    # ---- 判定（重要信号优先） ----
    if amz or ('pageNumber' in low and 'reviews' in low):
        return {'type': 'query_param', 'param': 'pageNumber',
                'hint': hints,
                'url_template': '{base}/product-reviews/{sku}?pageNumber={page}'}
    if m_param:
        param = m_param.group(1)
        return {'type': 'query_param', 'param': param,
                'hint': hints, 'url_template': ''}
    if m_path:
        return {'type': 'url_page', 'param': '',
                'hint': hints, 'url_template': ''}
    if rel_next and has_review_block:
        return {'type': 'query_param', 'param': 'page',
                'hint': hints, 'url_template': ''}
    if aria and has_review_block:
        return {'type': 'query_param', 'param': 'page',
                'hint': hints, 'url_template': ''}
    if more and has_review_block:
        return {'type': 'click_more', 'param': '',
                'hint': hints, 'url_template': ''}
    return {'type': 'none', 'param': '', 'hint': hints, 'url_template': ''}
def detect_multi_shop(html: str) -> dict:
    """检测商品页是否有「多店铺报价表」（kakaku 型聚合站）。"""
    low = (html or "").lower()
    if re.search(r"p-price(list|table|_row)|shop.?price|店名|コンテンツ一覧", low):
        return {"mode": "multi_shop", "signal": "price_table_detected"}
    return {"mode": "single_shop", "signal": ""}


def probe_product_page(pdp_html: str, pdp_url: str = "",
                       model: str = "", base_url: str = "") -> dict:
    """探商品页：价格/评分/规格/评价选择器命中情况。"""
    dom = LxmlDom.parse(pdp_html or "")
    if dom is None:
        return {"error": "pdp_html 无法解析", "pdp_url": pdp_url}
    want = _norm_model(model)
    h1 = (dom.text("h1") or "").strip()[:120]
    probes: dict[str, list] = {}
    for group, sels in {
        "price_selectors": ["[class*='price']", "[data-testid*='price']",
                            "span[class*='precio']", ".offer-price"],
        "rating_selectors": ["[class*='rating']", "[aria-label*='star']",
                             "[class*='review-count']"],
        "review_selectors": ["[class*='review']", "[data-testid*='review']"],
    }.items():
        for sel in sels:
            try:
                c = dom.count(sel)
            except Exception:
                c = 0
            if c:
                probes.setdefault(group, []).append(
                    {"selector": sel, "hits": c,
                     "sample": (dom.text(sel) or "")[:60]})
    return {
        "pdp_url": pdp_url,
        "h1": h1,
        "model_in_h1": bool(want and want in _norm_model(h1)),
        "spec_table_count": dom.count("table"),
        "detected": probes,
    }


def probe_retail_site(fetcher, site_code: str, model: str, *,
                      base_url: str = "", brand: str = "",
                      search_path: str = "",
                      want_reviews: bool = True) -> tuple[dict, str]:
    """在线探测一个零售站：搜索 → 排名候选 → 打开 top 候选商品页探测。

    返回 (report, best_html)。
    """
    if not base_url:
        raise ValueError(f"probe_retail_site 需要 base_url（{site_code}）")
    ensure_allowed(base_url)
    search_kw = f"{brand} {model}".strip() if brand else model
    url = _search_url(base_url, search_kw, search_path)

    from .scenarios import open_dom

    # 注意：类体不是闭包作用域，写 `base_url = base_url` 会走 LOAD_NAME 取不到
    # 外层函数局部变量而抛 NameError。必须先取到局部别名再赋值。
    _code_v, _base_v = site_code, base_url

    class _Adapter:
        code = _code_v
        base_url = _base_v
        protection = "L2"
        requires_browser = True
        schema = {"search": {}}

        def anchor(self) -> str:
            return ""

        def spec_wait_until(self) -> str:
            return "domcontentloaded"

    search_html = ""
    search_dom = None
    try:
        with open_dom(_Adapter(), fetcher, url) as (res, dom, html):
            if res and getattr(res, "ok", False) and dom is not None:
                search_html = html or ""
                search_dom = dom
    except Exception as e:
        return (build_retail_report(code=site_code, brand=brand, model=model,
                                    base_url=base_url, search_kw=search_kw,
                                    note=f"搜索异常 {type(e).__name__}: {e}"),
                "")

    if search_dom is None:
        return (build_retail_report(code=site_code, brand=brand, model=model,
                                    base_url=base_url, search_kw=search_kw,
                                    note="搜索页打不开/被拦"),
                search_html or "")

    ranked = _rank_on_dom(search_dom, _find_candidates(), _norm_model(model))
    best = ranked[0] if ranked else {}
    pdp_url = ""
    if best.get("sample"):
        pdp_url = best["sample"][0].split("|")[0]
        if pdp_url.startswith("/"):
            pdp_url = base_url.rstrip("/") + pdp_url

    pdp_probe = {}
    pdp_html = ""
    if pdp_url.startswith("http"):
        try:
            with open_dom(_Adapter(), fetcher, pdp_url) as (res2, dom2, html2):
                if html2:
                    pdp_html = html2
                    pdp_probe = probe_product_page(html2, pdp_url, model, base_url)
        except Exception as e:
            pdp_probe = {"pdp_url": pdp_url,
                         "error": f"PDP 探测异常 {type(e).__name__}"}
        else:
            if not pdp_probe:
                pdp_probe = {"pdp_url": pdp_url,
                             "note": "PDP 无 HTML（懒加载?需 BrowserFetcher）"}

    report = {
        "code": site_code,
        "brand": brand,
        "model": model,
        "base_url": base_url,
        "search_kw": search_kw,
        "search_url": url,
        "search": {
            "best_candidate_selector": best.get("selector", ""),
            "candidate_ranking": ranked[:6],
            "model_hit": best.get("model_hit", 0),
            "purity": best.get("purity", 0.0),
            "result_count": best.get("count", 0),
            "sample_pdp_hrefs": [s.split("|")[0] for s in best.get("sample", [])][:5],
        },
        "product_page": pdp_probe,
        "reviews": {"pagination_type": detect_review_pagination(
            pdp_html, has_review_block=bool(pdp_probe.get("detected")))},
        "shops": detect_multi_shop(pdp_html or ""),
    }
    return report, search_html or ""


def _search_url(base_url: str, kw: str, path: str = "") -> str:
    q = urllib.parse.quote(kw.strip())
    if path:
        base = base_url.rstrip("/")
        if "?" in path:
            return f"{base}/{path}&s={q}"
        return f"{base}/{path.strip('/')}?s={q}"
    return f"{base_url.rstrip('/')}/tienda?s={q}"


def _find_candidates() -> list[str]:
    return list(_SEARCH_CONTAINER_CANDIDATES)
