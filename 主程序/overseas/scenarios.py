"""采集场景编排。

场景是「站点适配器 + 抽取 schema + 存储」的流程组合：
    S1 关键词搜索采集   搜索 → 分页 → 详情 → 规格/价格/评价 → 入库
    S2 型号定向监控     型号清单 → 站内搜索匹配 → 锁定商品 → 价格快照
    S3 详情直采         已知 sku/URL → 详情（generic_jsonld 等无搜索站点用）
    S4 评价增量追踪     已入库商品 → 评价翻页 → 按 review_key 去重只入新增

所有场景都写 crawl_run / crawl_error 审计，失败单条不影响整体。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from . import config
from .db import Database
from .extract import Extractor
from .fetchers import BrowserFetcher, Dom, FetchResult, HttpFetcher
from .infra import BlockDetector, DiskCache, RateLimiter
from .models import PriceSnapshot, Product, ProductPayload, SearchHit
from .sites import SiteAdapter, SiteRegistry


@dataclass
class RunStats:
    ok: int = 0
    fail: int = 0
    blocked: int = 0
    products: int = 0
    reviews_new: int = 0
    prices: int = 0
    stopped: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"ok={self.ok} fail={self.fail} blocked={self.blocked} "
                f"products={self.products} prices={self.prices} "
                f"reviews_new={self.reviews_new}")


def make_fetcher(adapter: SiteAdapter, force_browser: bool = False):
    """按站点防护等级选 Fetcher，并用站点建议间隔（取更保守的一个）。"""
    interval = max(config.MIN_INTERVAL, adapter.suggested_interval)
    rate = RateLimiter(min_interval=interval)
    # 适配器可声明 block_marker_allowlist，豁免正常页面会内嵌的误报关键字
    ignore_markers = getattr(adapter, "block_marker_allowlist", None)
    detector = BlockDetector(ignore_markers=ignore_markers)
    shared = {"rate_limiter": rate, "detector": detector, "cache": DiskCache()}
    if adapter.requires_browser or force_browser:
        # site_code → 自动读写 data/session/<code>.state.json（登录态复用）
        return BrowserFetcher(site_code=getattr(adapter, "code", ""), **shared)
    return HttpFetcher(**shared)


@contextmanager
def open_dom(adapter: SiteAdapter, fetcher, url: str,
             wait_selector: str = "",
             settle_ms: int | None = None, scroll_passes: int | None = None,
             scroll_wait_ms: int | None = None,
             nav_timeout_ms: int | None = None,
             extra_wait_ms: int | None = None,
             click_texts: list[str] | None = None,
             ) -> Iterator[tuple[FetchResult, Dom | None, str]]:
    """统一「打开页面 → 拿 DOM 和 HTML」，屏蔽浏览器/HTTP 两条路径的差异。

    settle_ms/scroll_passes/scroll_wait_ms/nav_timeout_ms 为逐次等待覆盖（7.4），
    None 时沿用 config 默认；仅浏览器路径生效，不传时行为与原来一致。
    """
    if isinstance(fetcher, BrowserFetcher):
        wait_until = getattr(adapter, "spec_wait_until", "domcontentloaded")
        eff_extra_wait = 0
        eff_scroll = scroll_passes
        eff_click_selectors = None
        eff_click_repeats = None
        eff_click_wait = None
        eff_click_growth = ""
        eff_scroll_until_stable = False
        eff_scroll_growth_selector = ""
        eff_scroll_max_passes = None
        eff_scroll_stable_rounds = 2
        eff_capture_response_pattern = ""
        eff_capture_response_limit = 0
        is_entry = bool(wait_selector) and wait_selector == getattr(
            adapter, "spec_entry_wait", "")
        is_spec_page = bool(wait_selector) and wait_selector == getattr(
            adapter, "spec_page_wait", "")
        if is_entry:
            eff_extra_wait = int(getattr(adapter, "spec_entry_extra_wait_ms", 0) or 0)
            # 入口列表页可能懒加载（如 Samsung 全 TV 列表需滚动多次才加载全系列）
            esp = getattr(adapter, "spec_entry_scroll_passes", None)
            if esp and eff_scroll is None:
                eff_scroll = int(esp)
            # 入口页可声明 CSS 按钮循环点击（如 Hisense Load More）。
            eff_click_selectors = getattr(adapter, "spec_entry_click_selectors", None)
            eff_click_repeats = getattr(adapter, "spec_entry_click_repeats", None)
            eff_click_wait = getattr(adapter, "spec_entry_click_wait_ms", None)
            eff_click_growth = getattr(adapter, "spec_entry_click_growth_selector", "")
            eff_scroll_until_stable = bool(
                getattr(adapter, "spec_entry_scroll_until_stable", False))
            eff_scroll_growth_selector = getattr(
                adapter, "spec_entry_scroll_growth_selector", "")
            eff_scroll_max_passes = getattr(adapter, "spec_entry_scroll_max_passes", None)
            eff_scroll_stable_rounds = int(
                getattr(adapter, "spec_entry_scroll_stable_rounds", 2) or 2)
            eff_capture_response_pattern = getattr(
                adapter, "spec_entry_response_pattern", "") or ""
            eff_capture_response_limit = int(
                getattr(adapter, "spec_entry_response_limit", 0) or 0)
        elif is_spec_page:
            # SPEC 型号页可声明加大滚动/等待（如 Samsung 规格区需滚动 18+ 才渲染）
            sp = getattr(adapter, "spec_page_scroll_passes", None)
            if sp and eff_scroll is None:
                eff_scroll = int(sp)
            eff_extra_wait = int(getattr(adapter, "spec_page_extra_wait_ms", 0) or 0)
            if nav_timeout_ms is None:
                nav_timeout_ms = getattr(adapter, "spec_page_nav_timeout_ms", None)
        if extra_wait_ms is not None:                 # 显式覆盖（如详情页等评价懒加载）
            eff_extra_wait = max(eff_extra_wait, int(extra_wait_ms))
        # 入口和型号页的文本点击配置分开，避免 Hisense 入口误点 Full Specs。
        if click_texts is not None:
            eff_click = click_texts
        elif is_entry:
            eff_click = getattr(adapter, "spec_entry_click_texts", None)
        else:
            eff_click = getattr(adapter, "spec_click_texts", None)
        with fetcher.page(url, wait_selector=wait_selector,
                          wait_until=wait_until,
                          extra_wait_ms=eff_extra_wait,
                          settle_ms=settle_ms, scroll_passes=eff_scroll,
                          scroll_wait_ms=scroll_wait_ms,
                          nav_timeout_ms=nav_timeout_ms,
                          click_texts=eff_click,
                          click_selectors=eff_click_selectors,
                          click_repeats=eff_click_repeats,
                          click_wait_ms=eff_click_wait,
                          click_growth_selector=eff_click_growth,
                          scroll_until_stable=eff_scroll_until_stable,
                          scroll_growth_selector=eff_scroll_growth_selector,
                          scroll_max_passes=eff_scroll_max_passes,
                          scroll_stable_rounds=eff_scroll_stable_rounds,
                          capture_response_pattern=eff_capture_response_pattern,
                          capture_response_limit=eff_capture_response_limit) as (res, dom):
            html = ""
            if dom is not None:
                html = dom.html()
                res.html = html
            yield res, dom, html
    else:
        res = fetcher.get(url)
        dom = fetcher.dom(res) if res.html else None
        yield res, dom, res.html


def collect_detail(adapter: SiteAdapter, fetcher, sku: str,
                   want_reviews: bool = True,
                   spec: dict | None = None) -> tuple[ProductPayload | None, FetchResult]:
    """采集单个商品详情页，产出完整 payload。

    价格与网评在同一商品页同步抽取。评价区常是懒加载（如 Amazon 在页面底部），
    默认 3 次滚动够不到，故当 schema 定义了 reviews 且需要评价时，加大滚动次数
    以触发评价区渲染（可用 adapter.detail_scroll_passes 覆盖）。

    spec（可选）：传入 RetailSpec 时，抽取段（product/price/specs/summary/reviews）
    改用 spec 的六段定义驱动（RetailSpec 是 schema.json 的规范化 + 扩展，六段兼容），
    未传回落到 adapter.schema（向后兼容）。
    """
    url = sku if str(sku).startswith("http") else adapter.product_url(sku)
    scroll_passes = None
    detail_extra_wait = None
    schema = spec or adapter.schema
    if want_reviews and (schema or {}).get("reviews"):
        scroll_passes = int(getattr(adapter, "detail_scroll_passes", 14) or 14)
        # 滚动触发评价懒加载后，给评价区渲染留时间再抓 HTML
        detail_extra_wait = int(getattr(adapter, "detail_extra_wait_ms", 3500) or 0)
    with open_dom(adapter, fetcher, url, wait_selector=adapter.anchor(),
                  scroll_passes=scroll_passes,
                  extra_wait_ms=detail_extra_wait) as (res, dom, html):
        if res.blocked or dom is None or not res.ok:
            return None, res
        ex: Extractor = Extractor(schema, html=html,
                                  base_url=adapter.base_url)
        pf = ex.fields(dom, "product")
        prf = ex.fields(dom, "price")
        product = Product(
            site_code=adapter.code,
            sku=sku if not sku.startswith("http") else (pf.get("model") or sku)[:120],
            url=url,
            title=str(pf.get("title") or "")[:500],
            brand=str(pf.get("brand") or "")[:200],
            model=str(pf.get("model") or "")[:200],
            category=str(pf.get("category") or "")[:200],
            size=str(pf.get("size") or "")[:80],
        )
        price = PriceSnapshot(
            price=prf.get("price"),
            list_price=prf.get("list_price"),
            currency=str(prf.get("currency") or "")[:8],
            in_stock=prf.get("in_stock") if isinstance(prf.get("in_stock"), bool) else None,
            raw_text=str(prf.get("raw_text") or "")[:120],
        )
        payload = ProductPayload(
            product=product,
            specs=ex.specs(dom),
            price=price,
            summary=ex.summary(dom),
            reviews=ex.reviews(dom) if want_reviews else [],
        )
        return payload, res


def save_payload(db: Database, site_id: int, payload: ProductPayload,
                 stats: RunStats) -> int:
    """把 payload 落库，返回 product_id。"""
    pid = db.upsert_product(payload.product, site_id)
    stats.products += 1
    if payload.specs:
        db.save_specs(pid, payload.specs)
    if payload.price is not None and (payload.price.price is not None
                                     or payload.price.raw_text):
        db.add_price(pid, payload.price)
        stats.prices += 1
    if payload.summary is not None and (payload.summary.avg_rating is not None
                                        or payload.summary.total_count is not None):
        db.add_review_summary(pid, payload.summary)
    if payload.reviews:
        stats.reviews_new += db.add_reviews(pid, payload.reviews)
    if payload.ranking is not None:
        db.add_ranking(pid, payload.ranking)
    return pid


# ---------------------------------------------------------------- S1


def s1_keyword_search(site_code: str, keyword: str, limit: int = 10,
                      pages: int = 1, want_reviews: bool = True,
                      db: Database | None = None, verbose: bool = True) -> RunStats:
    """S1：关键词搜索采集（核心场景）。"""
    adapter = SiteRegistry.get(site_code)
    if not (adapter.schema or {}).get("search"):
        raise ValueError(f"{site_code} 不支持关键词搜索，请用 s3_detail")
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                             adapter.protection)
    run_id = db.start_run("s1_search", adapter.code,
                          {"keyword": keyword, "limit": limit, "pages": pages})
    fetcher = make_fetcher(adapter)
    try:
        hits: list[SearchHit] = []
        for page in range(1, max(1, pages) + 1):
            if len(hits) >= limit:
                break
            surl = adapter.search_url(keyword, page)
            if verbose:
                print(f"[搜索] p{page} {surl}")
            sel = (adapter.schema.get("search") or {}).get("container", "")
            with open_dom(adapter, fetcher, surl, wait_selector=sel) as (res, dom, html):
                if res.blocked:
                    stats.blocked += 1
                    db.log_error(run_id, surl, "search", "blocked", res.block_reason)
                    if verbose:
                        print(f"       被拦截：{res.block_reason}")
                    break
                if not res.ok or dom is None:
                    stats.fail += 1
                    db.log_error(run_id, surl, "search", "http",
                                 res.error or f"status={res.status}")
                    if verbose:
                        print(f"       失败：status={res.status} {res.error}")
                    continue
                page_hits = adapter.parse_search(dom, html, limit=limit - len(hits))
                if verbose:
                    print(f"       命中 {len(page_hits)} 个")
                if not page_hits:
                    break
                hits.extend(page_hits)

        if verbose:
            print(f"[详情] 待采集 {len(hits)} 个商品")
        consecutive_pdp_fail = 0   # 连续 PDP 失败计数（熔断用）
        consecutive_no_price = 0   # 连续无价格计数（空转检测）
        for i, hit in enumerate(hits[:limit], 1):
            try:
                payload, res = collect_detail(adapter, fetcher, hit.sku,
                                              want_reviews=want_reviews)
                if payload is None:
                    if res.blocked:
                        stats.blocked += 1
                        db.log_error(run_id, res.url, "detail", "blocked", res.block_reason)
                        consecutive_pdp_fail += 1
                    else:
                        stats.fail += 1
                        db.log_error(run_id, res.url, "detail", "http",
                                     res.error or f"status={res.status}")
                        consecutive_pdp_fail += 1
                    if verbose:
                        print(f"  {i}/{len(hits)} {hit.sku} 失败 "
                              f"{res.block_reason or res.error or res.status}")
                    # 熔断：PDP 连续被拦/失败超阈值 → 跳过剩余
                    if consecutive_pdp_fail >= config.MAX_CONSECUTIVE_PDP_BLOCKED:
                        if verbose:
                            print(f"  [熔断] PDP 连续 {consecutive_pdp_fail} 次失败，"
                                  f"跳过剩余 {len(hits) - i} 个商品")
                        db.log_error(run_id, adapter.product_url(hit.sku), "detail",
                                     "circuit_break", f"PDP连续失败{consecutive_pdp_fail}次，跳过剩余")
                        break
                    continue
                # PDP 成功，重置失败计数
                consecutive_pdp_fail = 0
                if not payload.product.title and hit.title:
                    payload.product.title = hit.title[:500]
                save_payload(db, site_id, payload, stats)
                stats.ok += 1
                # 空转检测：PDP 可达但无价格
                if payload.price is None or payload.price.price is None:
                    consecutive_no_price += 1
                else:
                    consecutive_no_price = 0
                if consecutive_no_price >= config.MAX_CONSECUTIVE_NO_PRICE:
                    if verbose:
                        print(f"  [空转检测] 连续 {consecutive_no_price} 个 PDP 无价格，"
                              f"该站可能不展示价格，跳过剩余")
                    db.log_error(run_id, adapter.product_url(hit.sku), "detail",
                                 "no_price_circuit", f"连续{consecutive_no_price}个PDP无价格，跳过剩余")
                    break
                if verbose:
                    print(f"  {i}/{len(hits)} {payload.stat()}")
            except Exception as e:                        # 单条失败不中断整体
                stats.fail += 1
                db.log_error(run_id, adapter.product_url(hit.sku), "detail",
                             "parse", f"{type(e).__name__}: {e}")
                consecutive_pdp_fail += 1
                if verbose:
                    print(f"  {i}/{len(hits)} {hit.sku} 异常 {type(e).__name__}: {e}")
                if consecutive_pdp_fail >= config.MAX_CONSECUTIVE_PDP_BLOCKED:
                    if verbose:
                        print(f"  [熔断] 连续异常 {consecutive_pdp_fail} 次，跳过剩余")
                    break
        db.finish_run(run_id, "completed", stats.ok, stats.fail, stats.summary())
        return stats
    except Exception as e:
        db.finish_run(run_id, "failed", stats.ok, stats.fail, f"{type(e).__name__}: {e}")
        raise
    finally:
        fetcher.close()
        if own_db:
            db.close()


# ---------------------------------------------------------------- S2


def _norm_model(s: str) -> str:
    """型号归一化：去掉空格/短横/下划线并大写，便于跨站比对。"""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def _pick_by_model(adapter: SiteAdapter, fetcher, model: str,
                   hits: list[SearchHit], max_detail_probe: int = 3,
                   verbose: bool = True):
    """在搜索结果里挑出「确实是该型号」的商品。

    搜索型号时 Amazon 常把配件排在前面（实测搜 OLED77G6WUA 首条是 $192.99 的配件），
    所以不能直接取 hits[0]。策略：
      1) 先按标题包含型号筛（便宜）
      2) 标题匹配不上时，逐个取详情页，用抽取出的 model 字段做权威校验
      3) 都对不上就返回 None，宁可不记也不写错数据
    返回 (payload, last_res, target_hit)
    """
    want = _norm_model(model)
    ordered = ([h for h in hits if want and want in _norm_model(h.title)]
               + [h for h in hits if not (want and want in _norm_model(h.title))])
    last_res = None
    probed = 0
    for hit in ordered:
        if probed >= max_detail_probe:
            break
        probed += 1
        payload, res = collect_detail(adapter, fetcher, hit.sku, want_reviews=False)
        last_res = res
        if payload is None:
            continue
        got = _norm_model(payload.product.model)
        title_n = _norm_model(payload.product.title)
        if want and (want == got or want in got or want in title_n):
            return payload, res, hit
        if verbose:
            print(f"      候选 {hit.sku} 型号={payload.product.model or '?'} 不匹配，继续")
    return None, last_res, None


def s2_monitor_known(site_code: str, limit: int = 200, db: Database | None = None,
                     verbose: bool = True) -> RunStats:
    """S2b：对已入库商品按 SKU 做价格快照（定时任务用这个）。

    比「按型号重新搜索」可靠得多：SKU 是站内唯一标识，不会误匹配到配件；
    也更省请求（省掉搜索页）。典型用法：先用 S1 发现商品，再由 timer 每天跑本场景。
    """
    adapter = SiteRegistry.get(site_code)
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                             adapter.protection)
    rows = db.products_for_monitor(site_code, limit)
    run_id = db.start_run("s2_monitor_known", adapter.code,
                          {"count": len(rows), "limit": limit})
    fetcher = make_fetcher(adapter)
    try:
        if verbose:
            print(f"[监控] 已入库商品 {len(rows)} 个")
        for i, row in enumerate(rows, 1):
            sku = row["sku"]
            try:
                payload, res = collect_detail(adapter, fetcher, sku, want_reviews=False)
                if payload is None:
                    stats.blocked += 1 if res.blocked else 0
                    stats.fail += 0 if res.blocked else 1
                    db.log_error(run_id, res.url, "detail",
                                 "blocked" if res.blocked else "http",
                                 res.block_reason or res.error or str(res.status))
                    if verbose:
                        print(f"  {i}/{len(rows)} {sku} 失败 "
                              f"{res.block_reason or res.error or res.status}")
                    continue
                save_payload(db, site_id, payload, stats)
                stats.ok += 1
                if verbose:
                    p = payload.price.price if payload.price else None
                    print(f"  {i}/{len(rows)} {sku} {payload.product.model or ''} 价格={p}")
            except Exception as e:
                stats.fail += 1
                db.log_error(run_id, sku, "detail", "parse", f"{type(e).__name__}: {e}")
                if verbose:
                    print(f"  {i}/{len(rows)} {sku} 异常 {type(e).__name__}: {e}")
        db.finish_run(run_id, "completed", stats.ok, stats.fail, stats.summary())
        return stats
    finally:
        fetcher.close()
        if own_db:
            db.close()


def s2_model_monitor(site_code: str, models: list[str], db: Database | None = None,
                     verbose: bool = True) -> RunStats:
    """S2：型号定向监控。按型号站内搜索，取最匹配的一个商品做价格快照。"""
    adapter = SiteRegistry.get(site_code)
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                             adapter.protection)
    run_id = db.start_run("s2_monitor", adapter.code, {"models": models})
    fetcher = make_fetcher(adapter)
    try:
        for i, model in enumerate([m.strip() for m in models if m.strip()], 1):
            try:
                surl = adapter.search_url(model)
                sel = (adapter.schema.get("search") or {}).get("container", "")
                with open_dom(adapter, fetcher, surl, wait_selector=sel) as (res, dom, html):
                    if res.blocked or not res.ok or dom is None:
                        stats.blocked += 1 if res.blocked else 0
                        stats.fail += 0 if res.blocked else 1
                        db.log_error(run_id, surl, "search",
                                     "blocked" if res.blocked else "http",
                                     res.block_reason or res.error or str(res.status))
                        if verbose:
                            print(f"  {i} {model} 搜索失败 "
                                  f"{res.block_reason or res.error or res.status}")
                        # 熔断：搜索连续被拦超阈值 → 跳过整站剩余型号
                        if res.blocked:
                            consecutive_search_blocked = getattr(
                                s2_model_monitor, '_consecutive_search_blocked', 0) + 1
                            setattr(s2_model_monitor, '_consecutive_search_blocked',
                                    consecutive_search_blocked)
                            if consecutive_search_blocked >= config.MAX_CONSECUTIVE_SEARCH_BLOCKED:
                                if verbose:
                                    print(f"  [熔断] 搜索连续 {consecutive_search_blocked} 次被拦，"
                                          f"跳过剩余 {len(models) - i} 个型号")
                                db.log_error(run_id, surl, "search", "circuit_break",
                                             f"搜索连续被拦{consecutive_search_blocked}次，跳过剩余")
                                break
                        continue
                    # 搜索成功，重置计数
                    setattr(s2_model_monitor, '_consecutive_search_blocked', 0)
                    hits = adapter.parse_search(dom, html, limit=5)
                if not hits:
                    stats.fail += 1
                    db.log_error(run_id, surl, "search", "parse", "无搜索结果")
                    if verbose:
                        print(f"  {i} {model} 无结果")
                    continue

                payload, res, target = _pick_by_model(
                    adapter, fetcher, model, hits, verbose=verbose)
                if payload is None:
                    # 宁可不记，也不能把配件的价格记成整机的价格
                    stats.fail += 1
                    reason = (res.block_reason or res.error or "型号未匹配"
                              if res is not None else "型号未匹配")
                    db.log_error(run_id, surl, "monitor", "no_match",
                                 f"{model}: {reason}")
                    if verbose:
                        print(f"  {i} {model} 未匹配到该型号商品（已跳过，不入库）")
                    continue
                if not payload.product.model:
                    payload.product.model = model[:200]
                save_payload(db, site_id, payload, stats)
                stats.ok += 1
                if verbose:
                    p = payload.price.price if payload.price else None
                    print(f"  {i} {model} → {target.sku} 价格={p}")
            except Exception as e:
                stats.fail += 1
                db.log_error(run_id, model, "monitor", "parse", f"{type(e).__name__}: {e}")
                if verbose:
                    print(f"  {i} {model} 异常 {type(e).__name__}: {e}")
        db.finish_run(run_id, "completed", stats.ok, stats.fail, stats.summary())
        return stats
    finally:
        fetcher.close()
        if own_db:
            db.close()


# ---------------------------------------------------------------- S3


def s3_detail(site_code: str, skus: list[str], want_reviews: bool = True,
              db: Database | None = None, verbose: bool = True) -> RunStats:
    """S3：详情直采。已知 sku（或完整 URL）时用，也用于无搜索能力的站点。"""
    adapter = SiteRegistry.get(site_code)
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                             adapter.protection)
    run_id = db.start_run("s3_detail", adapter.code, {"skus": skus})
    fetcher = make_fetcher(adapter)
    consecutive_pdp_fail = 0   # S3 也加熔断
    try:
        for i, sku in enumerate([s.strip() for s in skus if s.strip()], 1):
            try:
                payload, res = collect_detail(adapter, fetcher, sku,
                                              want_reviews=want_reviews)
                if payload is None:
                    stats.blocked += 1 if res.blocked else 0
                    stats.fail += 0 if res.blocked else 1
                    db.log_error(run_id, res.url, "detail",
                                 "blocked" if res.blocked else "http",
                                 res.block_reason or res.error or str(res.status))
                    if verbose:
                        print(f"  {i} {sku} 失败 "
                              f"{res.block_reason or res.error or res.status}")
                    consecutive_pdp_fail += 1
                    if consecutive_pdp_fail >= config.MAX_CONSECUTIVE_PDP_BLOCKED:
                        if verbose:
                            print(f"  [熔断] PDP 连续 {consecutive_pdp_fail} 次失败，"
                                  f"跳过剩余 {len(skus) - i} 个")
                        db.log_error(run_id, res.url, "detail", "circuit_break",
                                     f"PDP连续失败{consecutive_pdp_fail}次，跳过剩余")
                        break
                    continue
                consecutive_pdp_fail = 0
                save_payload(db, site_id, payload, stats)
                stats.ok += 1
                if verbose:
                    print(f"  {i} {payload.stat()}")
            except Exception as e:
                stats.fail += 1
                db.log_error(run_id, sku, "detail", "parse", f"{type(e).__name__}: {e}")
                consecutive_pdp_fail += 1
                if verbose:
                    print(f"  {i} {sku} 异常 {type(e).__name__}: {e}")
                if consecutive_pdp_fail >= config.MAX_CONSECUTIVE_PDP_BLOCKED:
                    if verbose:
                        print(f"  [熔断] 连续异常 {consecutive_pdp_fail} 次，跳过剩余")
                    break
        db.finish_run(run_id, "completed", stats.ok, stats.fail, stats.summary())
        return stats
    finally:
        fetcher.close()
        if own_db:
            db.close()


# ---------------------------------------------------------------- S4


def s4_review_incremental(site_code: str, skus: list[str], pages: int = 3,
                          db: Database | None = None, verbose: bool = True) -> RunStats:
    """S4：评价增量追踪。翻评价页，遇到一整页都是已入库评价就提前停。"""
    adapter = SiteRegistry.get(site_code)
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                             adapter.protection)
    run_id = db.start_run("s4_reviews", adapter.code,
                          {"skus": skus, "pages": pages})
    fetcher = make_fetcher(adapter)
    consecutive_review_blocked = 0   # S4 评价熔断
    try:
        for sku in [s.strip() for s in skus if s.strip()]:
            # 熔断检查：评价页连续被拦超阈值 → 跳过剩余
            if consecutive_review_blocked >= config.MAX_CONSECUTIVE_REVIEW_BLOCKED:
                if verbose:
                    print(f"[熔断] 评价页连续 {consecutive_review_blocked} 次被拦，"
                          f"跳过剩余评价抓取")
                db.log_error(run_id, "", "reviews", "circuit_break",
                             f"评价连续被拦{consecutive_review_blocked}次，跳过剩余")
                break
            pid = db.upsert_product(Product(site_code=adapter.code, sku=sku,
                                            url=adapter.product_url(sku)), site_id)
            known = db.existing_review_keys(pid)
            if verbose:
                print(f"[评价] {sku} 已入库 {len(known)} 条")
            for page in range(1, max(1, pages) + 1):
                rurl = adapter.reviews_url(sku, page)
                if not rurl:
                    if verbose:
                        print("       该站点无独立评价页，改用详情页评价")
                    payload, res = collect_detail(adapter, fetcher, sku, want_reviews=True)
                    if payload and payload.reviews:
                        stats.reviews_new += db.add_reviews(pid, payload.reviews)
                        stats.ok += 1
                    break
                cfg = adapter.schema.get("reviews") or {}
                with open_dom(adapter, fetcher, rurl,
                              wait_selector=cfg.get("container", "")) as (res, dom, html):
                    if res.blocked:
                        stats.blocked += 1
                        db.log_error(run_id, rurl, "reviews", "blocked", res.block_reason)
                        consecutive_review_blocked += 1
                        if verbose:
                            print(f"       p{page} 被拦截 {res.block_reason}")
                        break
                    if not res.ok or dom is None:
                        stats.fail += 1
                        db.log_error(run_id, rurl, "reviews", "http",
                                     res.error or str(res.status))
                        break
                    reviews = adapter.extractor(html).reviews(dom)
                if not reviews:
                    if verbose:
                        print(f"       p{page} 无评价，停止翻页")
                    break
                new_keys = [r for r in reviews if r.review_key not in known]
                inserted = db.add_reviews(pid, reviews)
                stats.reviews_new += inserted
                stats.ok += 1
                consecutive_review_blocked = 0   # 评价成功，重置熔断计数
                if verbose:
                    print(f"       p{page} 抓到 {len(reviews)} 条，新增 {inserted} 条")
                known.update(r.review_key for r in reviews)
                if not new_keys:
                    if verbose:
                        print("       本页全为已知评价，提前结束")
                    break
        db.finish_run(run_id, "completed", stats.ok, stats.fail, stats.summary())
        return stats
    finally:
        fetcher.close()
        if own_db:
            db.close()


# ---------------------------------------------------------------- 选择器自检


def probe_selectors(site_code: str, url: str) -> dict:
    """开发工具：对真实页面逐条检查 schema 里的选择器命中情况。

    DOM 改版时先跑这个定位坏掉的选择器，再改 schema.json。
    """
    adapter = SiteRegistry.get(site_code)
    fetcher = make_fetcher(adapter)
    out: dict = {"site": site_code, "url": url}
    try:
        with open_dom(adapter, fetcher, url, wait_selector=adapter.anchor()) as (res, dom, html):
            out["status"] = res.status
            out["title"] = res.title
            out["blocked"] = res.blocked
            out["block_reason"] = res.block_reason
            out["error"] = res.error
            if dom is None:
                return out
            ex = adapter.extractor(html)
            for group in ("product", "price", "summary"):
                g = {}
                for name, rule in (adapter.schema.get(group) or {}).items():
                    raw = ex.raw(dom, rule)
                    hit = ""
                    for sel in (rule.get("selectors") or []):
                        if dom.count(sel):
                            hit = sel
                            break
                    g[name] = {"value": (ex.value(dom, rule) if raw else None),
                               "matched_selector": hit or ("(kv/regex/jsonld)" if raw else None)}
                out[group] = g
            out["specs_found"] = len(ex.specs(dom))
            rc = (adapter.schema.get("reviews") or {}).get("container", "")
            out["reviews_container"] = rc
            out["reviews_blocks"] = dom.count(rc) if rc else 0
            out["reviews_parsed"] = len(ex.reviews(dom))
            sc = (adapter.schema.get("search") or {}).get("container", "")
            if sc:
                out["search_blocks_on_this_page"] = dom.count(sc)
        return out
    finally:
        fetcher.close()


# ---------------------------------------------------------------- SPEC 采集


def _iso_week(dt=None) -> str:
    """ISO 年-周，如 2026-W36。周度快照用它做键。"""
    from datetime import datetime
    d = dt or datetime.now()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _fetch_failure_kind(res: FetchResult) -> str:
    """把请求结果归类，供回退和熔断使用；404 等确定性结果不算瞬时故障。"""
    status = int(res.status or 0)
    if res.blocked:
        return "blocked"
    if status == 404:
        return "http_404"
    if status <= 0:
        text = (res.error or "").lower()
        return "timeout" if "timeout" in text or "timed out" in text else "network"
    if status == 408:
        return "http_408"
    if status == 429:
        return "http_429"
    if 500 <= status <= 599:
        return "http_5xx"
    if 400 <= status <= 499:
        return "http_4xx"
    return "http"


def _is_transient_kind(kind: str) -> bool:
    return kind in {"blocked", "network", "timeout", "http_408", "http_429", "http_5xx"}


def _is_transient_exception(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(token in text for token in (
        "timeout", "timed out", "connection", "target closed", "playwright",
        "browser", "net::",
    ))


def _record_spec_threshold_skips(adapter, remaining, week: str, run_id: int,
                                 db: Database, stats: RunStats, verbose: bool,
                                 threshold: int, multi: bool = False) -> None:
    """为熔断后未执行的系列补写失败审计，避免状态表出现静默缺口。"""
    for name, target in remaining:
        if multi:
            urls = [str(u) for u in (target or []) if u]
            requested_url = adapter.spec_entry_url()
            attempted = [{"url": u, "status": None,
                          "reason": "品牌连续失败熔断，未执行"} for u in urls]
            expected = len(urls)
            model_failed = expected
        else:
            requested_url = str(target or "")
            urls = [requested_url] if requested_url else []
            attempted = [{"url": requested_url, "status": None,
                          "reason": "品牌连续失败熔断，未执行"}] if requested_url else []
            expected = model_failed = 0
        message = f"连续 {threshold} 个系列发生瞬时失败，品牌熔断；本系列未执行"
        db.log_error(run_id, requested_url, "spec", "threshold_skip", message)
        db.record_spec_series_status(
            run_id=run_id,
            brand=adapter.code,
            brand_name=adapter.name,
            series=name,
            captured_week=week,
            status="failed",
            requested_url=requested_url,
            attempted_urls=attempted,
            model_expected=expected,
            model_failed=model_failed,
            error_kind="threshold_skip",
            message=message,
        )
        stats.fail += 1
        if verbose:
            print(f"  [SKIP] {name} {message}")


def spec_crawl(site_code: str, only_series: list[str] | None = None,
               db: Database | None = None, verbose: bool = True,
               adapter: SiteAdapter | None = None) -> RunStats:
    """SPEC 采集：遍历品牌全系列，抓官网规格表入库（周度快照）。

    only_series 非空时只抓指定系列（调试用）。d6.php 404 的系列自动跳过。
    404/空表是确定性结果，不触发品牌熔断；网络、超时、429/5xx/拦截等
    瞬时失败达到阈值后停止当前品牌剩余系列，并逐系列写入审计。

    adapter 非空时直接使用传入的适配器实例（供 GenericSpecAdapter 等配置驱动、
    未在 SiteRegistry 注册的适配器复用整条多级采集/审计/入库流程）。
    """
    if adapter is None:
        adapter = SiteRegistry.get(site_code)
    if not getattr(adapter, "supports_spec", False):
        raise ValueError(f"{site_code} 不是 SPEC 采集站点")
    own_db = db is None
    db = db or Database()
    stats = RunStats()
    week = _iso_week()
    run_id = db.start_run("spec_crawl", adapter.code, {"week": week,
                          "only": only_series or []})
    fetcher = make_fetcher(adapter)
    finished = False

    def _open(url, wait_selector=""):
        return open_dom(adapter, fetcher, url, wait_selector=wait_selector)

    try:
        # 多级流程（松下等）：入口→系列页→多型号页，一系列抓多型号后横排合并
        multi_entries = None
        try:
            entry_result = adapter.series_entries(_open)
            multi_entries = None if entry_result is None else list(entry_result)
        except NotImplementedError:
            multi_entries = None
        if multi_entries is not None:
            audit = getattr(adapter, "last_entry_audit", {}) or {}
            audit_details = audit.get("details")
            audit_details = dict(audit_details) if isinstance(audit_details, dict) else {}
            if not isinstance(audit_details.get("series"), list):
                discovered_series = []
                for series_name, model_urls in multi_entries:
                    models = []
                    for model_url in model_urls:
                        try:
                            model = str(adapter.model_from_url(model_url) or "").strip()
                        except Exception:
                            model = ""
                        if model and model != str(model_url):
                            models.append(model)
                    discovered_series.append({
                        "series": str(series_name),
                        "models": list(dict.fromkeys(models)),
                        "urls": [str(model_url) for model_url in model_urls],
                    })
                audit_details["series"] = discovered_series
            audit_details.setdefault("entry_series_count", len(multi_entries))
            audit_details.setdefault(
                "entry_model_count", sum(len(urls) for _, urls in multi_entries))
            db.record_spec_entry_audit(
                run_id=run_id,
                brand=adapter.code,
                brand_name=adapter.name,
                requested_url=str(audit.get("requested_url") or
                                  adapter.spec_entry_url()),
                expected_series_count=audit.get("expected_series_count"),
                discovered_series_count=int(audit.get("discovered_series_count") or
                                             len(multi_entries)),
                expected_model_count=audit.get("expected_model_count"),
                discovered_model_count=int(audit.get("discovered_model_count") or
                                           sum(len(urls) for _, urls in multi_entries)),
                termination_reason=str(audit.get("termination_reason") or "unknown"),
                details=audit_details,
            )
            if verbose:
                print(f"[SPEC] 入口审计：系列={audit.get('discovered_series_count', len(multi_entries))} "
                      f"型号={audit.get('discovered_model_count', sum(len(us) for _, us in multi_entries))} "
                      f"终止={audit.get('termination_reason', 'unknown')}")
            result = _spec_crawl_multi(adapter, fetcher, _open, multi_entries,
                                       only_series, week, run_id, db, stats, verbose)
            finished = True
            return result

        # 1) 入口页取系列列表
        entry_url = adapter.spec_entry_url()
        with open_dom(adapter, fetcher, entry_url,
                      wait_selector=getattr(adapter, "spec_entry_wait", "body")) as (res, dom, html):
            if not res.ok or dom is None:
                kind = _fetch_failure_kind(res)
                db.log_error(run_id, entry_url, "entry", kind,
                             res.error or res.block_reason or str(res.status))
                stats.fail += 1
                db.finish_run(run_id, "failed", stats.ok, stats.fail, "入口页失败")
                finished = True
                if verbose:
                    print(f"入口页失败 status={res.status} {kind}")
                return stats
            series = list(adapter.list_series(dom, html) or [])

        if not series:
            message = "入口未发现可采集系列"
            stats.fail += 1
            db.log_error(run_id, entry_url, "entry", "empty", message)
            db.finish_run(run_id, "failed", stats.ok, stats.fail, message)
            finished = True
            if verbose:
                print(f"[SPEC] {adapter.name} 入口抓取为空：未发现可采集系列")
            return stats

        want = {s.lower() for s in (only_series or [])}
        if want:
            series = [(n, u) for n, u in series if n.lower() in want]
            if not series:
                message = f"指定系列未匹配：{', '.join(only_series or [])}"
                stats.fail += 1
                db.log_error(run_id, entry_url, "entry", "empty", message)
                db.finish_run(run_id, "failed", stats.ok, stats.fail, message)
                finished = True
                if verbose:
                    print(f"[SPEC] {message}")
                return stats
        if verbose:
            print(f"[SPEC] {adapter.name} 周={week} 待抓系列 {len(series)} 个")

        # 2) 逐系列抓 SPEC 页；适配器可以提供多个候选 URL（如海信 d6/d5/d1）
        consecutive_series_failures = 0
        brand_stopped = False
        for i, (name, url) in enumerate(series, 1):
            series_transient = False
            try:
                candidates_fn = getattr(adapter, "spec_url_candidates", None)
                candidates = candidates_fn(name, url) if callable(candidates_fn) else [url]
                candidates = list(dict.fromkeys(str(candidate) for candidate in candidates if candidate))
                sheet = None
                used_url = url
                last_reason = ""
                attempted: list[dict] = []
                saw_http_ok = False
                saw_empty = False
                empty_kind = ""
                parse_error = False
                failure_kind = ""
                for candidate_url in candidates:
                    attempt = {"url": candidate_url, "status": None, "reason": ""}
                    attempted.append(attempt)
                    try:
                        with open_dom(
                            adapter,
                            fetcher,
                            candidate_url,
                            wait_selector=getattr(adapter, "spec_page_wait", "table"),
                        ) as (res, dom, html):
                            attempt["status"] = res.status
                            if res.status == 404:
                                failure_kind = "http_404"
                                last_reason = f"{candidate_url} status=404"
                                attempt["reason"] = last_reason
                                continue
                            if not res.ok or dom is None:
                                failure_kind = _fetch_failure_kind(res)
                                series_transient = series_transient or _is_transient_kind(failure_kind)
                                last_reason = (f"{candidate_url} "
                                               f"{res.error or res.block_reason or res.status}")
                                attempt["reason"] = last_reason
                                continue
                            saw_http_ok = True
                            if dom.count("table") == 0:
                                saw_empty = True
                                empty_kind = "no_table"
                                last_reason = f"{candidate_url} 无 table"
                                attempt["reason"] = last_reason
                                continue
                            candidate_sheet = adapter.parse_spec(dom, name, candidate_url)
                        if not candidate_sheet.rows or not candidate_sheet.models:
                            saw_empty = True
                            empty_kind = "no_rows_or_models"
                            last_reason = f"{candidate_url} 解析无行或无机型"
                            attempt["reason"] = last_reason
                            continue
                        sheet = candidate_sheet
                        used_url = candidate_url
                        attempt["reason"] = "success"
                        break
                    except Exception as candidate_error:
                        parse_error = True
                        failure_kind = "parse"
                        series_transient = series_transient or _is_transient_exception(candidate_error)
                        last_reason = (
                            f"{candidate_url} {type(candidate_error).__name__}: {candidate_error}"
                        )
                        attempt["reason"] = last_reason

                if sheet is None:
                    final_status = "empty" if saw_http_ok and saw_empty and not parse_error else "failed"
                    final_kind = empty_kind if final_status == "empty" else (failure_kind or "parse")
                    status_message = (
                        f"无有效 SPEC 候选页；尝试={','.join(candidates)}；最后原因={last_reason}"
                    )
                    stats.fail += 1
                    if final_kind == "blocked":
                        stats.blocked += 1
                    db.log_error(run_id, url, "spec", final_kind, status_message)
                    db.record_spec_series_status(
                        run_id=run_id,
                        brand=adapter.code,
                        brand_name=adapter.name,
                        series=name,
                        captured_week=week,
                        status=final_status,
                        requested_url=url,
                        attempted_urls=attempted,
                        error_kind=final_kind,
                        message=status_message,
                    )
                    if verbose:
                        label = "抓取为空" if final_status == "empty" else "抓取失败"
                        print(f"  {i}/{len(series)} {name} [{label}] {last_reason}")
                    if series_transient:
                        consecutive_series_failures += 1
                    else:
                        consecutive_series_failures = 0
                    if consecutive_series_failures >= config.MAX_CONSECUTIVE_SERIES_FAILURES:
                        brand_stopped = True
                        _record_spec_threshold_skips(
                            adapter, series[i:], week, run_id, db, stats, verbose,
                            config.MAX_CONSECUTIVE_SERIES_FAILURES,
                        )
                        break
                    continue

                if verbose and used_url != url:
                    print(f"  {i}/{len(series)} {name} 回退到 {used_url}")

                series_id = db.save_spec_sheet(sheet, week, replace_week=True)
                db.record_spec_series_status(
                    run_id=run_id,
                    brand=adapter.code,
                    brand_name=adapter.name,
                    series=name,
                    captured_week=week,
                    status="success",
                    requested_url=url,
                    selected_url=used_url,
                    attempted_urls=attempted,
                    series_id=series_id,
                    model_expected=len(sheet.models),
                    model_ok=len(sheet.models),
                    row_count=len(sheet.rows),
                    error_kind="fallback" if used_url != url else "",
                    message="候选规格页回退成功" if used_url != url else "",
                )
                for item in _spec_untranslated(sheet):
                    db.log_untranslated(item, adapter.code)
                stats.ok += 1
                stats.products += 1
                consecutive_series_failures = 0
                if verbose:
                    print(f"  {i}/{len(series)} {sheet.stat()}")
            except Exception as e:
                stats.fail += 1
                message = f"{type(e).__name__}: {e}"
                series_transient = _is_transient_exception(e)
                db.log_error(run_id, url, "spec", "parse", message)
                db.record_spec_series_status(
                    run_id=run_id,
                    brand=adapter.code,
                    brand_name=adapter.name,
                    series=name,
                    captured_week=week,
                    status="failed",
                    requested_url=url,
                    attempted_urls=[{"url": url, "status": None, "reason": message}],
                    error_kind="parse",
                    message=message,
                )
                if verbose:
                    print(f"  {i}/{len(series)} {name} [抓取失败] {message}")
                if series_transient:
                    consecutive_series_failures += 1
                else:
                    consecutive_series_failures = 0
                if consecutive_series_failures >= config.MAX_CONSECUTIVE_SERIES_FAILURES:
                    brand_stopped = True
                    _record_spec_threshold_skips(
                        adapter, series[i:], week, run_id, db, stats, verbose,
                        config.MAX_CONSECUTIVE_SERIES_FAILURES,
                    )
                    break

        if brand_stopped:
            stats.stopped = True
        db.finish_run(
            run_id,
            "failed" if brand_stopped else "completed",
            stats.ok,
            stats.fail,
            stats.summary(),
        )
        finished = True
        return stats
    except Exception as e:
        if not finished:
            stats.fail += 1
            message = f"{type(e).__name__}: {e}"
            try:
                db.log_error(run_id, getattr(adapter, "spec_entry_url", lambda: "")(),
                             "spec", "run", message)
            finally:
                db.finish_run(run_id, "failed", stats.ok, stats.fail, message)
            stats.stopped = True
        if verbose:
            print(f"[SPEC] 运行失败：{message}")
        return stats
    finally:
        fetcher.close()
        if own_db:
            db.close()


def _classify_spec_empty(adapter, dom, series: str, model: str, url: str) -> str:
    """调用站点可选的空 SPEC 诊断钩子，兼容未实现该钩子的旧适配器。"""
    classifier = getattr(adapter, "classify_spec_empty", None)
    if callable(classifier):
        try:
            reason = str(classifier(dom, series, model, url) or "").strip()
            if reason:
                return reason[:100]
        except Exception:
            # 诊断不能影响原有空页判定；解析失败仍按通用 empty 记录。
            pass
    return "empty"


def _spec_crawl_multi(adapter, fetcher, open_fn, entries, only_series, week,
                      run_id, db, stats, verbose):
    """多级 SPEC 采集：一系列抓多个型号页 → 横排合并成一张表入库。"""
    from .spec_parser import merge_single_model_sheets

    want = {s.lower() for s in (only_series or [])}
    if want:
        entries = [(n, us) for n, us in entries if n.lower() in want]
    if not entries:
        message = f"指定系列未匹配：{', '.join(only_series or [])}" if want else "入口未发现可采集系列"
        stats.fail += 1
        db.log_error(run_id, adapter.spec_entry_url(), "entry", "empty", message)
        db.finish_run(run_id, "failed", stats.ok, stats.fail, message)
        stats.stopped = True
        if verbose:
            print(f"[SPEC] {message}")
        return stats
    if verbose:
        total_models = sum(len(us) for _, us in entries)
        print(f"[SPEC] {adapter.name} 周={week} 待抓系列 {len(entries)} 个 "
              f"（型号页 {total_models} 个）")

    consecutive_series_failures = 0
    brand_stopped = False
    model_threshold = max(1, config.MAX_CONSECUTIVE_MODEL_FAILURES)
    for i, (series, model_urls) in enumerate(entries, 1):
        model_expected = len(model_urls)
        model_ok = 0
        model_empty = 0
        model_failed = 0
        model_consecutive_failures = 0
        series_transient = False
        threshold_skip = False
        error_messages: list[str] = []
        model_empty_kinds: list[str] = []
        model_failure_kinds: list[str] = []
        single_sheets = []
        try:
            for model_index, murl in enumerate(model_urls):
                model = ""
                try:
                    model = adapter.model_from_url(murl)
                    with open_fn(murl, getattr(adapter, "spec_page_wait", "table")) as (res, dom, html):
                        if res.status == 404 or dom is None or not res.ok:
                            kind = "http_404" if res.status == 404 else _fetch_failure_kind(res)
                            model_failure_kinds.append(kind)
                            if kind == "blocked":
                                stats.blocked += 1
                            transient = _is_transient_kind(kind)
                            series_transient = series_transient or transient
                            model_consecutive_failures = (
                                model_consecutive_failures + 1 if transient else 0
                            )
                            model_failed += 1
                            reason = res.error or res.block_reason or str(res.status)
                            error_messages.append(f"{model or murl}: {reason}")
                            db.log_error(run_id, murl, "spec", kind, reason)
                            if verbose:
                                print(f"  {i}/{len(entries)} {series}/{model} 型号页[抓取失败] status={res.status}")
                            if model_consecutive_failures >= model_threshold:
                                threshold_skip = True
                                for skipped_url in model_urls[model_index + 1:]:
                                    model_failed += 1
                                    skip_message = (
                                        f"系列连续 {model_threshold} 个型号发生瞬时失败，"
                                        "剩余型号未执行"
                                    )
                                    error_messages.append(
                                        f"{adapter.model_from_url(skipped_url) or skipped_url}: {skip_message}"
                                    )
                                    db.log_error(run_id, skipped_url, "spec",
                                                 "threshold_skip", skip_message)
                                break
                            continue
                        sh = adapter.parse_spec_model(dom, series, model, murl)
                    if not sh.rows or not sh.models:
                        empty_kind = _classify_spec_empty(
                            adapter, dom, series, model, murl
                        )
                        model_empty += 1
                        model_empty_kinds.append(empty_kind)
                        model_consecutive_failures = 0
                        error_messages.append(
                            f"{model or murl}: 无行或无机型（{empty_kind}）"
                        )
                        db.log_error(
                            run_id, murl, "spec", empty_kind,
                            f"型号页解析为空（{empty_kind}）",
                        )
                        if verbose:
                            print(
                                f"  {i}/{len(entries)} {series}/{model} "
                                f"型号页[抓取为空:{empty_kind}]"
                            )
                        continue
                    model_ok += 1
                    model_consecutive_failures = 0
                    single_sheets.append(sh)
                except Exception as model_error:
                    kind = "timeout" if _is_transient_exception(model_error) else "parse"
                    model_failure_kinds.append(kind)
                    transient = _is_transient_kind(kind)
                    series_transient = series_transient or transient
                    model_consecutive_failures = (
                        model_consecutive_failures + 1 if transient else 0
                    )
                    model_failed += 1
                    message = f"{model or murl}: {type(model_error).__name__}: {model_error}"
                    error_messages.append(message)
                    db.log_error(run_id, murl, "spec", kind, message)
                    if verbose:
                        print(f"  {i}/{len(entries)} {series}/{model} 型号页[抓取失败] {message}")
                    if model_consecutive_failures >= model_threshold:
                        threshold_skip = True
                        for skipped_url in model_urls[model_index + 1:]:
                            model_failed += 1
                            skip_message = (
                                f"系列连续 {model_threshold} 个型号发生瞬时失败，剩余型号未执行"
                            )
                            error_messages.append(
                                f"{adapter.model_from_url(skipped_url) or skipped_url}: {skip_message}"
                            )
                            db.log_error(run_id, skipped_url, "spec",
                                         "threshold_skip", skip_message)
                        break

            if not single_sheets:
                final_status = (
                    "empty"
                    if model_expected == 0 or (model_empty and not model_failed and not threshold_skip)
                    else "failed"
                )
                distinct_empty_kinds = {
                    kind for kind in model_empty_kinds if kind and kind != "empty"
                }
                if threshold_skip:
                    final_kind = "threshold_skip"
                elif model_expected == 0:
                    final_kind = "no_models"
                elif final_status == "empty" and len(distinct_empty_kinds) == 1:
                    final_kind = next(iter(distinct_empty_kinds))
                elif final_status == "empty":
                    final_kind = "no_rows"
                elif model_failure_kinds and all(
                    kind == "blocked" for kind in model_failure_kinds
                ):
                    final_kind = "blocked"
                else:
                    final_kind = "model_http_or_parse"
                message = f"{series} 无有效型号页；" + "；".join(error_messages)
                stats.fail += 1
                db.log_error(run_id, adapter.spec_entry_url(), "spec", final_kind, message)
                db.record_spec_series_status(
                    run_id=run_id,
                    brand=adapter.code,
                    brand_name=adapter.name,
                    series=series,
                    captured_week=week,
                    status=final_status,
                    requested_url=adapter.spec_entry_url(),
                    attempted_urls=list(model_urls),
                    model_expected=model_expected,
                    model_ok=model_ok,
                    model_empty=model_empty,
                    model_failed=model_failed,
                    error_kind=final_kind,
                    message=message,
                )
                if verbose:
                    label = "抓取为空" if final_status == "empty" else "抓取失败"
                    print(f"  {i}/{len(entries)} {series} [{label}]，跳过")
            else:
                sheet = merge_single_model_sheets(
                    single_sheets, adapter.code, adapter.name, series,
                    url=single_sheets[0].url if single_sheets else "")
                if not sheet.rows or not sheet.models:
                    final_status = "empty" if not model_failed and not threshold_skip else "failed"
                    final_kind = "merge_empty" if final_status == "empty" else (
                        "threshold_skip" if threshold_skip else "merge_failed"
                    )
                    message = f"{series} 合并后为空；" + "；".join(error_messages)
                    stats.fail += 1
                    db.log_error(run_id, adapter.spec_entry_url(), "spec", final_kind, message)
                    db.record_spec_series_status(
                        run_id=run_id,
                        brand=adapter.code,
                        brand_name=adapter.name,
                        series=series,
                        captured_week=week,
                        status=final_status,
                        requested_url=adapter.spec_entry_url(),
                        attempted_urls=list(model_urls),
                        model_expected=model_expected,
                        model_ok=model_ok,
                        model_empty=model_empty,
                        model_failed=model_failed,
                        error_kind=final_kind,
                        message=message,
                    )
                else:
                    partial = model_ok < model_expected or model_empty > 0 or model_failed > 0
                    final_status = "failed" if partial else "success"
                    final_kind = "threshold_skip" if threshold_skip else (
                        "partial_models" if partial else ""
                    )
                    message = "；".join(error_messages) if error_messages else ""
                    series_id = db.save_spec_sheet(sheet, week, replace_week=not partial)
                    db.record_spec_series_status(
                        run_id=run_id,
                        brand=adapter.code,
                        brand_name=adapter.name,
                        series=series,
                        captured_week=week,
                        status=final_status,
                        requested_url=adapter.spec_entry_url(),
                        selected_url=single_sheets[0].url if single_sheets else "",
                        attempted_urls=list(model_urls),
                        series_id=series_id,
                        model_expected=model_expected,
                        model_ok=model_ok,
                        model_empty=model_empty,
                        model_failed=model_failed,
                        row_count=len(sheet.rows),
                        error_kind=final_kind,
                        message=message,
                    )
                    for item in _spec_untranslated(sheet):
                        db.log_untranslated(item, adapter.code)
                    stats.products += 1
                    if final_status == "success":
                        stats.ok += 1
                    else:
                        stats.fail += 1
                    if verbose:
                        label = "" if final_status == "success" else " [抓取失败：部分型号未完成]"
                        print(f"  {i}/{len(entries)} {sheet.stat()}{label}")
        except Exception as e:
            stats.fail += 1
            message = f"{series}: {type(e).__name__}: {e}"
            series_transient = _is_transient_exception(e)
            db.log_error(run_id, adapter.spec_entry_url(), "spec", "parse", message)
            db.record_spec_series_status(
                run_id=run_id,
                brand=adapter.code,
                brand_name=adapter.name,
                series=series,
                captured_week=week,
                status="failed",
                requested_url=adapter.spec_entry_url(),
                attempted_urls=list(model_urls),
                model_expected=model_expected,
                model_ok=model_ok,
                model_empty=model_empty,
                model_failed=max(1, model_failed),
                error_kind="parse",
                message=message,
            )
            if verbose:
                print(f"  {i}/{len(entries)} {series} [抓取失败] {message}")

        if series_transient:
            consecutive_series_failures += 1
        else:
            consecutive_series_failures = 0
        if consecutive_series_failures >= config.MAX_CONSECUTIVE_SERIES_FAILURES:
            brand_stopped = True
            _record_spec_threshold_skips(
                adapter, entries[i:], week, run_id, db, stats, verbose,
                config.MAX_CONSECUTIVE_SERIES_FAILURES, multi=True,
            )
            break

    if brand_stopped:
        stats.stopped = True
    db.finish_run(
        run_id,
        "failed" if brand_stopped else "completed",
        stats.ok,
        stats.fail,
        stats.summary(),
    )
    return stats


def _spec_untranslated(sheet) -> list[str]:
    from .spec_parser import collect_untranslated
    return collect_untranslated(sheet)

