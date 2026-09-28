"""发现阶段：陌生网站结构探测（Probe）。

给一个入口 URL，自动摸清"电视总览页"的结构，输出一份结构化探测报告
（probe_report），供 spec_generator.generate_spec 让 LLM 总结成 AdapterSpec。

探测内容：
    1. 候选产品链接选择器：用一组通用 CSS 候选在真实 DOM 上统计命中数，
       选出最像"产品卡链接"的选择器；
    2. 产品 URL 样本与 URL 模式：为 LLM 推断 model_url_regex 提供依据；
    3. 分页/加载方式判断：静态链接数 →（滚动/点 view more/翻页）后链接数，
       增长则判定该站需要对应的动态加载（scroll / click_more / paginate）；
    4. 精简 HTML：prune_html 后的入口页，供实测打分 score_spec_on_html 使用。

两种运行方式：
    - 在线：传入 fetcher（BrowserFetcher/HttpFetcher），本模块负责打开页面；
    - 离线：直接传入 entry_html（已渲染），用于测试/无浏览器环境。

本模块只探测与汇报，不生成 schema、不抓取、不写库。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from .fetchers import LxmlDom
from .spec_generator import prune_html

# 通用"产品卡链接"候选选择器（按站点常见模式排序；命中最多且 URL 像产品页者胜出）
_LINK_SELECTOR_CANDIDATES = [
    "a[href*='/c-p/']",           # Philips
    "a[href*='/dp/']",            # Amazon
    "a[href*='/p/']",
    "a[href*='/product/']",
    "a[href*='/bravia/products/']",  # Sony BRAVIA（比全站 /products/ 更专，排除耳机/相机等）
    "a[href*='/tv/lineup/']",        # REGZA 电视 lineup（比裸 /lineup/ 更专，排除 bd-dvd 等）
    "a[href*='/products/']",
    "a[href*='/tv/']",
    "a[href*='/tvs/']",
    "a[href*='/televisions/']",
    "a[href*='/tv-soundbars/']",  # LG
    "a[href*='/product-page/']",  # Wix 建站（Hisense US 等）
    "a.product-card",
    "a.product-item",
    "[class*='product'] a[href]",
    "[class*='card'] a[href]",
    "article a[href]",
    "li a[href]",
]

# 非电视链接的负向关键词（避免把 soundbar/配件当产品）
_NON_TV_RE = re.compile(
    r"soundbar|speaker|headphone|remote|bracket|mount|accessor|cable|"
    r"projector|monitor|/support|/help|/account|/cart|/search",
    re.I,
)

# 产品 URL 里像"型号"的片段（字母数字混合、长度≥4）
_MODEL_HINT_RE = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*|"
                            r"[A-Za-z]{2,}\d{2,}[A-Za-z0-9]*")
# 末段 slug 是否像"具体型号页"：要求出现"字母紧邻≥2位数字"的片段，
# 如 75u7sg / 43qd40r / tv43qd40r。这样能把真型号与分类/导航词（4k-uled、
# 4k-uhd、smart-tv-platforms）区分开——后者只有孤立单数字，不含字母+2位数字连写。
_MODEL_SLUG_RE = re.compile(r"[A-Za-z]\d{2,}|\d{2,}[A-Za-z]")


def _host(url: str) -> str:
    return urlsplit(str(url or "")).netloc.lower()


def _looks_like_model_url(href: str) -> bool:
    """判断链接末段是否像"具体型号页"（含字母+数字混合的 slug）。"""
    path = urlsplit(str(href or "")).path.rstrip("/")
    last = path.rsplit("/", 1)[-1] if path else ""
    return bool(last and _MODEL_SLUG_RE.search(last))


def _rank_link_selectors(dom: LxmlDom) -> list[dict]:
    """在 DOM 上评估每个候选选择器，返回按"产品相关度"降序的统计。

    排序不只看命中数：导航/分类的泛选择器（li a[href]）常命中最多，却指向
    /televisions/4k-uled 这类分类页；真正的产品链接（/product-page/tv43qd40r）
    数量可能更少但 URL 末段像"型号"。因此优先按"型号样式链接数"排序，命中总数
    仅作次级依据——避免导航泛链接把真实产品选择器淹没（Hisense US Wix 站即此坑）。
    """
    stats: list[dict] = []
    for sel in _LINK_SELECTOR_CANDIDATES:
        try:
            hrefs = dom.attr_list(sel, "href", limit=3000)
        except Exception:
            hrefs = []
        if not hrefs:
            continue
        # 去重 + 过滤明显非电视链接
        uniq: list[str] = []
        seen: set[str] = set()
        for h in hrefs:
            hl = h.lower()
            if _NON_TV_RE.search(hl):
                continue
            key = hl.split("?")[0].rstrip("/")
            if key and key not in seen:
                seen.add(key)
                uniq.append(h)
        if uniq:
            model_like = sum(1 for h in uniq if _looks_like_model_url(h))
            purity = model_like / len(uniq)
            stats.append({"selector": sel, "unique_links": len(uniq),
                          "model_like_links": model_like,
                          "model_like_ratio": round(purity, 3),
                          "sample": uniq[:5]})
    # 排序信号是"型号纯度"而非绝对命中数：产品链接选择器命中的链接几乎都是
    # 型号页（纯度高），导航泛选择器（li a[href]）混入大量分类/其它品类链接
    # （纯度低）。以纯度为主键、命中数为次键；但纯度只在 model_like 达到最小
    # 门槛（≥3）时才主导，避免只命中 1~2 个链接的选择器凭 100% 纯度虚高胜出。
    def _rank_key(s: dict) -> tuple:
        strong = s["model_like_links"] >= 3
        return (1 if strong else 0, s["model_like_ratio"] if strong else 0.0,
                s["model_like_links"], s["unique_links"])
    stats.sort(key=_rank_key, reverse=True)
    return stats


def _url_pattern(sample_urls: list[str]) -> str:
    """从样本 URL 归纳一个粗略路径模式，帮 LLM 写 model_url_regex。"""
    if not sample_urls:
        return ""
    path = urlsplit(sample_urls[0]).path
    # 把明显是型号的段替换成占位
    segs = [s for s in path.split("/") if s]
    out = []
    for s in segs:
        if _MODEL_HINT_RE.fullmatch(s) or _MODEL_HINT_RE.search(s):
            out.append("<MODEL...>")
        else:
            out.append(s)
    return "/" + "/".join(out)


def _count_for(dom: LxmlDom, selector: str) -> int:
    try:
        return len({h.lower().split("?")[0].rstrip("/")
                    for h in dom.attr_list(selector, "href", limit=3000)
                    if not _NON_TV_RE.search(h.lower())})
    except Exception:
        return 0


def build_probe_report(entry_url: str, entry_html: str, *,
                       code: str = "", brand_name: str = "", region: str = "",
                       expected_model_count: int | None = None,
                       after_scroll_html: str = "",
                       after_more_html: str = "") -> dict:
    """由入口页 HTML（可选滚动/加载后的 HTML）构造探测报告。

    after_scroll_html / after_more_html 若提供，则与初始 HTML 的产品链接数对比，
    判断该站是否需要滚动或 view more/翻页动态加载。
    """
    dom = LxmlDom.parse(entry_html or "")
    if dom is None:
        return {"error": "lxml 未安装或入口 HTML 解析失败",
                "code": code, "entry_url": entry_url}

    ranked = _rank_link_selectors(dom)
    best = ranked[0] if ranked else None
    best_selector = best["selector"] if best else ""
    static_count = best["unique_links"] if best else 0
    samples = best["sample"] if best else []

    # 分页/加载方式判断：对比初始 vs 滚动后 vs 加载更多后的链接数
    load_hint = "none"
    growth = {}
    if best_selector:
        if after_scroll_html:
            dom_s = LxmlDom.parse(after_scroll_html)
            if dom_s is not None:
                n = _count_for(dom_s, best_selector)
                growth["after_scroll"] = n
                if n > static_count * 1.2:
                    load_hint = "scroll"
        if after_more_html:
            dom_m = LxmlDom.parse(after_more_html)
            if dom_m is not None:
                n = _count_for(dom_m, best_selector)
                growth["after_more"] = n
                if n > max(static_count, growth.get("after_scroll", 0)) * 1.2:
                    load_hint = "click_more_or_paginate"

    # 分页控件检测：把 load_hint 提升为分页，并汇报页码信号，让 LLM 知道需累积式分页。
    # 关键区分（避免误判静态站）：
    #   - 明确的"编号页码"（Show page N）是**可靠**分页信号，即使替换式渲染链接不增长，
    #     也判为 paginate_replace；
    #   - 仅有孤立"下一页/next"按钮而**无编号页码**时，极易被轮播/推荐区的 next 按钮误伤
    #     （如 Sony BRAVIA lineup 静态列全，却有推荐轮播的 next）。此时只有在链接确实随
    #     交互增长（load_hint 已是 scroll/click_more）才当分页，否则保持 none，不逼 LLM 加分页。
    pager = _detect_pager(entry_html)
    if load_hint == "none" and pager["has_numbered_pages"]:
        load_hint = "paginate_replace"

    report = {
        "code": code,
        "brand_name": brand_name,
        "region": region,
        "base_url": f"{urlsplit(entry_url).scheme}://{_host(entry_url)}" if entry_url else "",
        "entry_url": entry_url,
        "candidate_hosts": [_host(entry_url)] if entry_url else [],
        "entry_dom_summary": {
            "best_link_selector": best_selector,
            "static_unique_links": static_count,
            "link_selector_ranking": ranked[:6],
            "sample_product_hrefs": samples,
            "product_url_pattern": _url_pattern(samples),
            "load_more_hint": load_hint,
            "link_growth": growth,
            "pager": pager,
        },
        "xhr_endpoints": [],  # 预留：在线模式可由 capture_response 填充
        "expected": {"model_count": expected_model_count},
    }
    return report


# 分页控件的 aria-label / class 特征（用于识别"该站是分页站"）
_NUMBERED_PAGE_RE = re.compile(r"aria-label\s*=\s*[\"'][^\"']*\bpage\s+\d+\b[^\"']*[\"']", re.I)
_NEXT_BTN_RE = re.compile(r"aria-label\s*=\s*[\"'][^\"']*\b(?:next\s+page|next)\b[^\"']*[\"']", re.I)
_LOADMORE_RE = re.compile(r"(?:aria-label|class)\s*=\s*[\"'][^\"']*(?:load\s*more|show\s*more)[^\"']*[\"']", re.I)


def _detect_pager(html: str) -> dict:
    """从入口 HTML 检测分页控件特征，判断该站是否为分页站及分页形态。"""
    text = str(html or "")
    numbered = _NUMBERED_PAGE_RE.findall(text)
    return {
        "has_numbered_pages": bool(numbered),
        "numbered_page_count": len(set(numbered)),
        "has_next_button": bool(_NEXT_BTN_RE.search(text)),
        "has_load_more": bool(_LOADMORE_RE.search(text)),
    }


# 通用"加载更多 / 下一页"按钮候选（探测阶段尝试点击，把懒加载/分页内容load 出来）。
# 纯 CSS（lxml 校验器与 Playwright 均支持），不含 :has-text 之类 Playwright 伪类。
_LOAD_MORE_CANDIDATES = [
    "button[aria-label*='more' i]",
    "button[class*='load-more' i]",
    "a[class*='load-more' i]",
    "[data-hook='load-more-button']",
    "button[class*='show-more' i]",
    "a[class*='show-more' i]",
    "[aria-label*='Show page' i]",
    "a[rel='next']",
    "a[class*='next' i]",
    "li[class*='next' i] a",
    "button[class*='pagination' i]",
]


def probe_site(fetcher, entry_url: str, *, code: str = "", brand_name: str = "",
               region: str = "", expected_model_count: int | None = None,
               wait_selector: str = "a[href]",
               scroll_passes: int = 6,
               try_load_more: bool = True) -> tuple[dict, str]:
    """在线探测：用 fetcher 打开入口页，采集初始 / 滚动后 / 加载更多后的 HTML。

    分两趟摸索该站的动态加载方式：
      1. 仅滚动到稳定（scroll_until_stable）→ scrolled_html；
      2. 若 try_load_more，再打开一次并额外尝试点击常见"加载更多/下一页"按钮
         （通用候选选择器循环点击）→ more_html。
    build_probe_report 会对比三趟的链接数，判定 load_more_hint（none/scroll/
    click_more_or_paginate）。返回 (probe_report, best_html)，best_html 取链接最多
    的那趟，供 score_spec_on_html 实测打分与后续发现。
    """
    from .scenarios import open_dom  # 延迟导入避免循环

    class _Scroll:
        spec_entry_wait = wait_selector
        spec_entry_extra_wait_ms = 3000
        spec_entry_scroll_until_stable = True
        spec_entry_scroll_growth_selector = "a[href]"
        spec_entry_scroll_max_passes = scroll_passes
        spec_entry_scroll_stable_rounds = 2

    scrolled_html = ""
    with open_dom(_Scroll(), fetcher, entry_url, wait_selector=wait_selector) as (res, dom, html):
        if dom is not None and getattr(res, "ok", False):
            scrolled_html = html or ""

    more_html = ""
    if try_load_more:
        class _More:
            spec_entry_wait = wait_selector
            spec_entry_extra_wait_ms = 3000
            spec_entry_scroll_until_stable = True
            spec_entry_scroll_growth_selector = "a[href]"
            spec_entry_scroll_max_passes = scroll_passes
            spec_entry_scroll_stable_rounds = 2
            # 循环点击"加载更多/下一页"候选，检测链接增长
            spec_entry_click_selectors = list(_LOAD_MORE_CANDIDATES)
            spec_entry_click_repeats = 12
            spec_entry_click_wait_ms = 1500
            spec_entry_click_growth_selector = "a[href]"

        try:
            with open_dom(_More(), fetcher, entry_url, wait_selector=wait_selector) as (res, dom, html):
                if dom is not None and getattr(res, "ok", False):
                    more_html = html or ""
        except Exception:
            more_html = ""

    # 选链接最多的一趟作为 best_html（发现/打分用最全的那份）
    candidates = [h for h in (more_html, scrolled_html) if h]
    best_html = max(candidates, key=len) if candidates else ""
    report = build_probe_report(
        entry_url, scrolled_html or best_html, code=code, brand_name=brand_name,
        region=region, expected_model_count=expected_model_count,
        after_scroll_html=scrolled_html, after_more_html=more_html)
    return report, (best_html or scrolled_html)


def pruned_entry_html(entry_html: str, max_chars: int = 30000) -> str:
    """探测报告配套：喂 LLM 的精简入口 HTML。"""
    return prune_html(entry_html, max_chars=max_chars)
