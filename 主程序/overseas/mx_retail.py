"""墨西哥线零售抓取编排：按「型号 × 启用零售站」在同一商品页同步抓价格 + 网评。

设计要点（尽量复用日本线核心，不重造轮子）：
- 定位商品：复用 scenarios._pick_by_model（「以型号开头 > 独立词 > 子串 > 详情页
  model 权威校验 > 兜底不记」），避免把配件价记成整机价；
- 同步抓取：复用 scenarios.collect_detail —— 它在一次页面抽取里同时产出
  price 与 reviews，正是「价格 + 网评同一商品页同步抓取」；
- 补齐评价：零售站若提供独立评价页（reviews_url 非空，如 Amazon），在商品页抓完后
  复用 s4 的翻页增量逻辑补齐评价；商品页即含评价的站点（reviews_url 空）直接用同步结果；
- 落库/状态：复用 db.save_payload 落库；型号 × 零售站状态复用 kakaku_task_status
  （task="price"，brand=<零售站code> 承载零售站维度），不新增表；
- 币种：墨西哥线统一强制 MXN（零售站 schema 也用 currency_mx，双保险）；
- 数据隔离：数据库/产物由调用方（scripts/mx_line.py）指定到 data/mx/，不与其它线重叠。

本模块只做编排，不改动 scenarios / db / 导出层，因此不影响日本线与加拿大/美国线。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from . import config
from .db import Database
from .extract import Extractor
from .models import Review, SearchHit
from .regions_config import get_region
from .retail_spec import collect_reviews_paged, resolve_max_reviews
from .review_export import ReviewRecord
from .scenarios import (RunStats, collect_detail, make_fetcher, open_dom,
                        save_payload, _pick_by_model)
from .sites import SiteAdapter, SiteRegistry

MX_CURRENCY = "MXN"
MX_COUNTRY = "Mexico"


def resolve_region_currency(region_code: str, explicit: str = "") -> str:
    """解析本线币种：显式入参 > region 声明 > MXN（墨西哥线历史默认）。

    本模块原先在 `crawl_model_on_site` 里硬编码 `MX_CURRENCY`，直接复用到美国线
    会把 USD 价格打上 MXN 标签。抽成纯函数便于离线回归，也便于个别线覆盖
    （如巴拿马用 USD 但 region 声明不同）。
    """
    if (explicit or "").strip():
        return explicit.strip()
    region = get_region(region_code)
    declared = (getattr(region, "currency", "") or "").strip() if region else ""
    return declared or MX_CURRENCY


@dataclass
class RetailRow:
    """一次「型号 × 零售站」抓取的结构化结果（供导出与状态统计）。"""
    brand: str = ""            # 品牌展示名，如 Hisense
    brand_code: str = ""       # 品牌 code，如 hisense_mx
    model: str = ""            # 型号
    site: str = ""             # 零售站 code，如 liverpool_mx
    channel: str = ""          # 渠道展示名，如 Liverpool
    size: str = ""
    price: float | None = None
    list_price: float | None = None
    currency: str = MX_CURRENCY
    in_stock: bool | None = None
    sku: str = ""
    url: str = ""
    status: str = ""           # ok / no_item / no_price / no_reviews / blocked / failed
    reviews: list[Review] = field(default_factory=list)
    shop_prices: dict = field(default_factory=dict)  # {渠道/店铺: 价}（multi_shop 用）
    lowest_shop: str = ""      # 最低价对应店铺/渠道（溯源列）
    # 评价阶段被反爬时的原因（价格可能已抓到，故不推翻整行 status）
    review_block_reason: str = ""
    # 评价终止原因：max_reviews / page_all_known / max_pages / blocked / ...
    review_stop_reason: str = ""


def _search_landed_pdp(adapter, res, dom, html: str, model: str) -> list[SearchHit]:
    """搜索直达详情页兜底：页面无搜索卡片但实际落在商品 PDP 时构造单候选。

    识别条件（liverpool 实测）：当前页 URL 是 /tienda/pdp/...（或含 pdp 路径）且
    存在 h1 标题。返回含一个 SearchHit 的列表；不能确认是 PDP 时返回 []。
    """
    import re
    url = str(getattr(res, "url", "") or "")
    # 1) 从 URL / canonical / og:url 提取规范 PDP 链接
    pdp_url = ""
    if "/pdp/" in url:
        pdp_url = url.split("?")[0]
    for pat in (r'rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
                r'property=["\']og:url["\']\s+content=["\']([^"\']+)["\']'):
        if not pdp_url:
            m = re.search(pat, html or "", re.I)
            if m:
                pdp_url = m.group(1).split("?")[0]
    if not pdp_url or "/pdp/" not in pdp_url:
        return []
    # 2）标题：优先 h1，否则 canonical 末段 decode 补
    title = ""
    try:
        from .fetchers import LxmlDom
        parsed = LxmlDom.parse(html or "")
        if parsed is not None:
            title = (parsed.text("h1") or "").strip()
    except Exception:
        title = ""
    if not title:
        seg = pdp_url.rstrip("/").split("/")[-1]
        title = seg.replace("-", " ").title()
    return [SearchHit(sku=pdp_url, url=pdp_url, title=title, rank=1)]


def _brand_of_model(region, model: str) -> tuple[str, str]:
    """尽力从型号推断所属品牌（用于导出「品牌」列）。取不到返回空。

    型号清单来自 SPEC 型号全集（含 brand_code），调用方通常已带品牌；这里仅在
    调用方未提供品牌时兜底（返回第一个品牌名之类不做猜测，直接留空）。
    """
    return "", ""


def _parse_search_with_spec(retail_spec: dict, adapter,
                            dom, html: str, limit: int = 5) -> list:
    """按 RetailSpec 的 search 段提取搜索候选（镜像基类 parse_search，读 spec）。

    无 schema.json 的站（thebrick_ca / leons_ca 等只有 adapter.py）传 retail_spec
    时，搜索候选的容器/标题/url 规则都在 spec 里——不再依赖 adapter.schema。
    """
    from .sites import SiteAdapter
    cfg = (retail_spec or {}).get("search") or {}
    container = cfg.get("container") or ""
    if dom is None or not container:
        return []
    ex = Extractor(retail_spec or {}, html=html, base_url=adapter.base_url)
    tmpl = cfg.get("url_template") or ""
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in dom.sub(container, limit=max(limit * 3, limit)):
        sku = str(ex.raw(block, cfg.get("sku") or {}) or "").strip()
        if not sku or sku in seen:
            continue
        # 去查询串与锚点：搜索卡片的 href 常带追踪参数（?_pos=N&_sid=...），
        # 直接当 PDP URL 用会 404/session 错。
        sku = sku.split("?")[0].split("#")[0]
        seen.add(sku)
        title = ""
        if cfg.get("title"):
            title = str(ex.value(block, cfg["title"]) or "")
        url = ""
        if cfg.get("url"):
            url = str(ex.raw(block, cfg["url"]) or "")
            if url.startswith("/"):
                url = adapter.base_url.rstrip("/") + url
        if not url and tmpl:
            url = tmpl.format(sku=sku.replace("/pdp/", ""))
        hits.append(SearchHit(sku=sku, url=url or sku, title=title,
                              rank=len(hits) + 1))
        if len(hits) >= limit:
            break
    return hits


def crawl_model_on_site(site_code: str, model: str, *, brand: str = "",
                        brand_code: str = "", want_reviews: bool = True,
                        review_pages: int | None = None,
                        max_reviews: int | None = None,
                        db: Database | None = None,
                        run_id: int | None = None, save_db: bool = False,
                        stats: RunStats | None = None,
                        retail_spec: dict | None = None,
                        currency: str = "",
                        verbose: bool = True) -> RetailRow:
    """在单个零售站抓单个型号：搜索定位 → 同步抓价格+评价 →（可选）补齐评价。

    返回 RetailRow；同时（save_db 时）落库并写型号×零售站状态。
    retail_spec 传入时用配置化 match_product 三段匹配（应用 reject/宁缺毋滥），
    未传回落到既有 _pick_by_model 详情页校验路径（向后兼容）。
    currency：本线币种（US=USD / PE=PEN …）。留空回落 MXN，保持墨西哥线原行为。
    """
    adapter = SiteRegistry.get(site_code)
    row = RetailRow(brand=brand, brand_code=brand_code, model=model,
                    site=site_code, channel=adapter.channel or adapter.name)
    # 币种必须由调用方按 region 传入：本模块原先硬编码 MXN，直接复用到美国线会把
    # USD 价格打上 MXN 标签（南美线靠导出层另写币种绕过，但 --save-db 入库仍写错）。
    eff_currency = (currency or "").strip() or MX_CURRENCY
    row.currency = eff_currency
    # 合规白名单准入（P1-3）：未登记站点直接拒绝，不发起任何请求
    try:
        from .retail_compliance import ensure_allowed
        ensure_allowed(adapter.base_url, check_robots=True)
    except Exception as e:
        row.status = "blocked"
        reason = str(e)[:120]
        if verbose:
            print(f"  [{site_code}] {model} 合规校验不过：{reason}")
        _record(db, run_id, site_code, adapter, model, row, "blocked", reason, save_db)
        return row
    fetcher = make_fetcher(adapter)
    own_db = db is None
    if save_db and db is None:
        db = Database()
        db.init_schema()
    site_id = None
    if save_db and db is not None:
        site_id = db.upsert_site(adapter.code, adapter.name, adapter.base_url,
                                 adapter.protection)
    try:
        # 1) 站内搜索该型号。优先用 spec 的 search_keyword_template 拼品牌+型号
        # （如 liverpool 裸型号搜索会 302 跳到精确匹配 PDP，带品牌才返回候选列表）。
        search_kw = model
        if retail_spec is not None:
            tmpl = ((retail_spec.get("match") or {}).get("search_keyword_template")
                    or (retail_spec.get("search") or {}).get("keyword_template") or "")
            if tmpl:
                search_kw = tmpl.format(brand=brand or "", model=model).strip()
        surl = adapter.search_url(search_kw)
        # 搜索候选容器：优先 spec 的 search 段，无 spec 回落 adapter.schema
        search_cfg = (retail_spec or adapter.schema or {}).get("search") or {}
        sel = search_cfg.get("container", "")
        try:
            with open_dom(adapter, fetcher, surl, wait_selector=sel) as (res, dom, html):
                if not res.ok or dom is None:
                    row.status = "failed"
                    _record(db, run_id, site_code, adapter, model, row, "failed",
                            res.error or f"status={res.status}", save_db)
                    if verbose:
                        print(f"  [{site_code}] {model} 搜索失败 status={res.status}")
                    return row
                # 候选提取：retail_spec 存在时按 spec 六段（search 段）驱动
                if retail_spec is not None:
                    hits = _parse_search_with_spec(retail_spec, adapter, dom, html,
                                                   limit=5)
                else:
                    hits = adapter.parse_search(dom, html, limit=5)
                if not hits:
                    # 搜索直达 PDP 兜底：部分单店站（liverpool 实测）裸型号搜索会
                    # 302 直达商品详情页（无搜索卡片容器 → no_anchor_element 误判
                    # blocked）。此时用规范 URL + h1 标题构造单候选继续走型号匹配。
                    hits = _search_landed_pdp(adapter, res, dom, html, model)
                if res.blocked and hits:
                    # no_anchor 但页面实际是搜索直达的 PDP（有候选）→ 不视为拦截
                    if verbose:
                        print(f"  [{site_code}] {model} 搜索直达详情页（{res.block_reason}）"
                              f"，按候选处理")
                elif res.blocked:
                    row.status = "blocked"
                    if verbose:
                        print(f"  [{site_code}] {model} 搜索被拦截：{res.block_reason}")
                    _record(db, run_id, site_code, adapter, model, row, "blocked",
                            res.block_reason or "blocked", save_db)
                    return row
        except Exception as e:
            row.status = "failed"
            _record(db, run_id, site_code, adapter, model, row, "failed",
                    f"{type(e).__name__}: {e}", save_db)
            if verbose:
                print(f"  [{site_code}] {model} 搜索异常 {type(e).__name__}: {e}")
            return row

        if not hits:
            row.status = "no_item"
            _record(db, run_id, site_code, adapter, model, row, "no_item",
                    "无搜索结果", save_db)
            if verbose:
                print(f"  [{site_code}] {model} 无搜索结果")
            return row

        # 2) 挑出确实是该型号的商品，并在同一商品页同步抓价格 + 评价
        payload = None
        last_res = None
        target = None
        if retail_spec is not None:
            # 配置化匹配（任务表 P0-4）：三段兜底 + reject，纯文本判定
            from .retail_spec import match_product
            target = match_product(retail_spec, hits, brand=brand, model=model)
            if target is not None:
                if verbose:
                    print(f"  [{site_code}] {model} → {target.sku}")
        else:
            # 既有路径：_pick_by_model 抓详情做权威校验（标题型号都对得上）
            payload, last_res, target = _pick_by_model(
                adapter, fetcher, model, hits, verbose=verbose)
        if payload is None and target is not None:
            # 配置化匹配命中后仍要取完整详情（价格 + 评价）
            # 优先用 target.url（搜索时已补全的完整链路，可能含 /products/ 等路径），
            # 避免 adapter.product_url(sku) 对"相对路径 sku"二次拼路径出错（404）。
            detail_sku = getattr(target, "url", "") or target.sku
            if not str(detail_sku).startswith("http"):
                detail_sku = target.sku
            payload, last_res = collect_detail(adapter, fetcher, detail_sku,
                                               want_reviews=want_reviews,
                                               spec=retail_spec)
        if payload is None or target is None:
            row.status = "no_item"
            reason = "型号未匹配到商品"
            if last_res is not None and last_res.blocked:
                row.status = "blocked"
                reason = last_res.block_reason or "blocked"
            _record(db, run_id, site_code, adapter, model, row, row.status,
                    reason, save_db)
            if verbose:
                print(f"  [{site_code}] {model} 未匹配到该型号（不抓价，避免记成配件）")
            return row
        if payload is None:
            row.status = "blocked" if (last_res and last_res.blocked) else "failed"
            _record(db, run_id, site_code, adapter, model, row, row.status,
                    (last_res.block_reason or last_res.error or "详情抓取失败") if last_res else "详情抓取失败",
                    save_db)
            return row

        # 3) 填充结构化结果（价格 + 评价，均来自同一商品页）
        if not payload.product.model:
            payload.product.model = model[:200]
        row.sku = payload.product.sku
        row.url = payload.product.url
        row.size = payload.product.size or ""
        if payload.price is not None:
            row.price = payload.price.price
            row.list_price = payload.price.list_price
            row.in_stock = payload.price.in_stock
        row.currency = eff_currency          # 按 region 解析（MX=MXN / US=USD …）
        if payload.price is not None:
            payload.price.currency = eff_currency
        row.reviews = list(payload.reviews or [])

        # 4) 评价翻页补齐（R1-4：spec 驱动四种 paginate.reviews.type；
        #    spec 未配时回落 adapter.reviews_url，兼容存量站点）
        if want_reviews:
            row.reviews, review_meta = collect_reviews_paged(
                retail_spec or {}, adapter, fetcher, row.sku or model,
                row.reviews, open_dom=open_dom, product_url=row.url,
                max_reviews=max_reviews, max_pages=review_pages, verbose=verbose)
            # 反爬如实上报，不静默（供站级熔断与任务表汇报）。价格已抓到时不推翻
            # 整行状态，只在 message 里带出，避免丢掉已有价格数据。
            if review_meta.get("blocked"):
                row.review_block_reason = (review_meta.get("block_reason")
                                           or "blocked")
            row.review_stop_reason = str(review_meta.get("stop_reason") or "")
            if verbose and row.review_stop_reason:
                print(f"       评价终止：{row.review_stop_reason}"
                      f"（{len(row.reviews)} 条 / 取页 {review_meta['pages_fetched']}"
                      f" / 上限 {review_meta['max_reviews']}）")
            payload.reviews = row.reviews

        # 5) 落库 + 状态
        if save_db and db is not None and site_id is not None:
            save_payload(db, site_id, payload, stats or RunStats())
        has_price = row.price is not None
        has_reviews = bool(row.reviews)
        if not has_price and not has_reviews:
            row.status = "no_price"
        elif not has_price:
            row.status = "no_price"
        elif not has_reviews:
            row.status = "no_reviews"
        else:
            row.status = "ok"
        _record(db, run_id, site_code, adapter, model, row, row.status,
                "", save_db, product_size=row.size,
                review_count=len(row.reviews))
        if verbose:
            print(f"  [{site_code}] {model} → {row.sku} "
                  f"价格={row.price} 评价={len(row.reviews)} 状态={row.status}")
        return row
    finally:
        fetcher.close()
        if own_db and save_db and db is not None:
            db.close()


def _augment_reviews(adapter, fetcher, sku: str, base: list[Review],
                     pages: int = 2, verbose: bool = True) -> list[Review]:
    """【已弃用，保留兼容】独立评价页翻页补齐。

    R1-4 起改用 `retail_spec.collect_reviews_paged`：它支持 spec 的四种
    `paginate.reviews.type`、总量上限（默认 100，可配置）、页数安全阀，
    并把 blocked/stop_reason 结构化返回以便汇报与站级熔断。
    本函数只按 adapter.reviews_url 翻页、无总量封顶、失败静默 break，勿在新代码使用。
    """
    seen = {r.review_key for r in base}
    out = list(base)
    for page in range(1, max(1, pages) + 1):
        rurl = adapter.reviews_url(sku, page)
        if not rurl:
            break
        cfg = adapter.schema.get("reviews") or {}
        try:
            with open_dom(adapter, fetcher, rurl,
                          wait_selector=cfg.get("container", "")) as (res, dom, html):
                if res.blocked or not res.ok or dom is None:
                    break
                page_reviews = adapter.extractor(html).reviews(dom)
        except Exception:
            break
        if not page_reviews:
            break
        fresh = [r for r in page_reviews if r.review_key not in seen]
        for r in fresh:
            seen.add(r.review_key)
            out.append(r)
        if not fresh:                 # 整页都是已知评价，提前停
            break
    return out


def _record(db, run_id, site_code, adapter, model, row: RetailRow, status: str,
            message: str, save_db: bool, product_size: str = "",
            review_count: int = 0) -> None:
    """写型号×零售站状态到 kakaku_task_status（task=price，brand=零售站code）。

    复用现有状态表，不新增表；零售站维度由 brand 字段承载。save_db 关时不落库。
    """
    if not save_db or db is None:
        return
    try:
        db.record_kakaku_status(
            task="price",
            brand=site_code,                      # 零售站 code 作为维度键
            model=model,
            status=status,
            run_id=run_id,
            brand_name=adapter.channel or adapter.name,
            item_id=row.sku,
            size=product_size or row.size,
            row_count=1 if row.price is not None else 0,
            new_count=review_count,
            message=message,
        )
    except Exception:
        # 状态记录失败不影响主流程（价格/评价已抓到）
        pass


def crawl_region_retail(region_code: str, models: list[dict], *,
                        sites: Iterable[str] | None = None,
                        want_reviews: bool = True,
                        review_pages: int | None = None,
                        max_reviews: int | None = None,
                        blocked_streak_limit: int = 5,
                        db: Database | None = None, save_db: bool = False,
                        retail_spec: dict | None = None,
                        currency: str = "",
                        verbose: bool = True) -> list[RetailRow]:
    """region 主编排：型号全集 × 启用零售站，逐一同步抓价格+评价。

    models：来自 SPEC 型号全集，每条至少含 "model_raw"/"model", "brand_code",
            "brand_name"；只用其型号与品牌信息，不依赖具体文件格式。
    sites：限定零售站 code 集合；缺省用 region 中 enabled=True 的零售站。
    retail_spec：可选 RetailSpec，传入时匹配走配置化 match_product（任务表 P0-4），
            未传回落到既有 _pick_by_model 详情页校验路径（向后兼容）。
    """
    region = get_region(region_code)
    if region is None:
        raise ValueError(f"未知 region: {region_code}")
    site_codes = list(sites) if sites else region.retail_site_codes(only_enabled=True)
    # 币种按 region 解析（US=USD / PE=PEN …），避免把美国价格写成 MXN。
    eff_currency = resolve_region_currency(region_code, currency)
    if not site_codes:
        if verbose:
            print(f"[MX] region={region_code} 无启用零售站")
        return []

    own_db = db is None
    if save_db and db is None:
        db = Database()
        db.init_schema()
    run_id = None
    if save_db and db is not None:
        run_id = db.start_run("mx_retail", region_code,
                              {"sites": site_codes, "models": len(models)})
    stats = RunStats()
    rows: list[RetailRow] = []
    try:
        cap_reviews = resolve_max_reviews(retail_spec, max_reviews)
        for site_code in site_codes:
            if verbose:
                print(f"[MX] 零售站 {site_code}：{len(models)} 个型号"
                      f"（评价上限 {cap_reviews} 条/型号）")
            # R1-12 站级熔断：连续 N 个型号被拦截即跳过该站剩余型号，
            # 避免被墙的站把整份型号清单逐个撞一遍（防死循环的关键一环）。
            blocked_streak = 0
            login_wall_streak = 0
            for m in models:
                model = str(m.get("model_raw") or m.get("model") or "").strip()
                if not model:
                    continue
                brand_code = str(m.get("brand_code") or "")
                brand_name = str(m.get("brand_name") or "")
                row = crawl_model_on_site(
                    site_code, model, brand=brand_name, brand_code=brand_code,
                    want_reviews=want_reviews, review_pages=review_pages,
                    max_reviews=max_reviews,
                    db=db, run_id=run_id, save_db=save_db, stats=stats,
                    retail_spec=retail_spec, currency=eff_currency, verbose=verbose)
                rows.append(row)
                # 站级熔断：blocked 或 HTTP 403/410/429 连续失败都算。
                # 例外：not_logged_in 是"会话失效"，不是反爬——撞熔断只会把
                # 621 个型号静默全跳过（历史事故）。它单独计数并明确提示补登录态。
                is_login_wall = ("not_logged_in" in (row.review_block_reason or "")
                                 or "not_logged_in" in (row.fail_msg or ""))
                is_block_like = (not is_login_wall) and (
                    row.status == "blocked"
                    or bool(row.review_block_reason)
                    or (row.status == "failed"
                        and any(c in row.fail_msg
                                for c in ("403", "410", "429"))))
                if is_login_wall:
                    login_wall_streak += 1
                    if login_wall_streak == 1 and verbose:
                        print(f"  [{site_code}] 命中登录墙（会话失效/未登录）→ "
                              f"该站需要先补登录态：python -m overseas.cli login "
                              f"--site {site_code}")
                else:
                    login_wall_streak = 0
                if is_block_like:
                    blocked_streak += 1
                    if blocked_streak >= max(1, blocked_streak_limit):
                        if verbose:
                            print(f"  [{site_code}] 连续 {blocked_streak} 个型号被拦截/限流，"
                                  f"站级熔断，跳过该站剩余型号")
                        break
                else:
                    blocked_streak = 0
        if save_db and db is not None and run_id is not None:
            ok = sum(1 for r in rows if r.status == "ok")
            fail = sum(1 for r in rows if r.status in {"failed", "blocked"})
            db.finish_run(run_id, "completed", ok, fail,
                          f"rows={len(rows)} ok={ok}")
        return rows
    finally:
        if own_db and save_db and db is not None:
            db.close()


def rows_to_review_records(rows: list[RetailRow]) -> list[ReviewRecord]:
    """把抓取结果展开为通用网评模板记录（20 列，AI 三列留空由 NULL_HOOKS 处理）。"""
    out: list[ReviewRecord] = []
    for r in rows:
        for rv in r.reviews:
            out.append(ReviewRecord(
                country=MX_COUNTRY,
                brand=r.brand or r.brand_code,
                model=r.model,
                channel=r.channel,
                size=r.size,
                rating=rv.rating if rv.rating is not None else "",
                title=rv.title,
                body=rv.body,
                helpful=rv.helpful_count if rv.helpful_count is not None else "",
                review_time=rv.review_date,
                review_url=rv.review_url,
                image_count=rv.image_count,
                image_urls=" | ".join(rv.image_urls),
                has_video="是" if rv.has_video else "否",
                video_urls=" | ".join(rv.video_urls),
                item_id=r.sku,
                price=("" if r.price is None else f"{r.price:g}"),
                # 翻译 / 提炼优点 / 提炼缺点：本阶段留空（NULL_HOOKS），保留接入点
                translation="",
                ai_pros="",
                ai_cons="",
            ))
    return out


# ==================== 墨西哥价格导出 ====================
# 对齐日本线导出模板：price_export.PriceRecord + write_csv/write_xlsx
# （唯一差异：店铺段按 spec 动态生成，日本 kakaku=8 店铺列，墨西哥 single_shop=渠道/现价）。
# 传 retail_spec 时走动态列；不传时回落日本 8 店铺列（向后兼容）——由 price_export.build_columns 决定。


def rows_to_price_records(rows: list[RetailRow],
                          retail_spec: dict | None = None) -> list:
    """把 RetailRow 列表转成 price_export.PriceRecord（对齐日本线价格表模板）。

    single_shop（自营站）：渠道即店铺 → channel 填渠道，店铺段出「渠道/现价」两列；
    multi_shop（聚合站）：把 r.shop_prices 按列映射展开（P0-2 已由调用方抽出）。
    """
    from datetime import datetime

    from .price_export import PriceRecord

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    out: list[PriceRecord] = []
    for r in rows:
        if r.price is None and not r.reviews and r.status not in {"ok", "no_reviews"}:
            # 无价且无评价的失败/无货记录不进输出（状态表里可查渠道）
            if r.status not in {"no_reviews"}:
                continue
        lowest_shop = r.lowest_shop or (r.channel if r.status in ("ok", "no_reviews") else "")
        rec = PriceRecord(
            region="墨西哥",
            brand=r.brand or r.brand_code or r.model[:4],
            model=r.model,
            size=r.size,
            release="",
            currency=r.currency,
            lowest_price=r.price,
            lowest_shop=lowest_shop,
            channel=r.channel,
            item_id=r.sku,
            url=r.url,
            captured_at=ts,
        )
        rec.shop_prices = dict(r.shop_prices or {})
        out.append(rec)
    return out


def write_mx_price_csv(path, rows: list[RetailRow],
                       retail_spec: dict | None = None) -> int:
    """价格监控 CSV：复用 price_export（对齐日本价格表模板，按品牌分块标题行）。"""
    from .price_export import write_csv as _pe_write_csv
    recs = rows_to_price_records(rows, retail_spec)
    return _pe_write_csv(path, recs, blocked=True, spec=retail_spec)


def write_mx_price_xlsx(path, rows: list[RetailRow],
                        retail_spec: dict | None = None) -> int:
    """价格监控 XLSX：复用 price_export（对齐日本价格表模板）。"""
    from .price_export import write_xlsx as _pe_write_xlsx
    recs = rows_to_price_records(rows, retail_spec)
    return _pe_write_xlsx(path, recs, blocked=True, spec=retail_spec)
