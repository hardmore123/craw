"""命令行入口。

    python -m overseas.cli init-db
    python -m overseas.cli sites
    python -m overseas.cli config
    python -m overseas.cli search --site amazon_ca --keyword "hisense tv" --limit 5
    python -m overseas.cli detail --site amazon_ca --sku B0GPHMTMLC
    python -m overseas.cli monitor --site amazon_ca --model OLED77G6WUA --model 65U8N
    python -m overseas.cli reviews --site amazon_ca --sku B0GPHMTMLC --pages 2
    python -m overseas.cli probe  --site amazon_ca --url https://www.amazon.ca/dp/B0GPHMTMLC
    python -m overseas.cli login  --site amazon_ca       # 人工登录，存登录态（可复用）
    python -m overseas.cli login-status                  # 看已保存的登录态
    python -m overseas.cli products --site amazon_ca
    python -m overseas.cli history --product-id 1
    python -m overseas.cli stats
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, config
from .db import Database
from .sites import SiteRegistry


def _p(*a):
    print(*a, flush=True)


def cmd_config(args) -> int:
    config.ensure_dirs()
    _p(f"overseas {__version__}")
    for k, v in config.summary().items():
        _p(f"  {k:20} = {v}")
    return 0


def cmd_init_db(args) -> int:
    config.ensure_dirs()
    with Database(args.db) as db:
        v = db.init_schema()
        _p(f"数据库已初始化: {db.path}")
        _p(f"schema 版本: {v}")
        _p(f"统计: {db.stats()}")
    return 0


def cmd_sites(args) -> int:
    metas = SiteRegistry.all_meta()
    if args.json:
        _p(json.dumps(metas, ensure_ascii=False, indent=2))
        return 0
    _p(f"{'code':16} {'防护':5} {'浏览器':7} {'搜索':5} {'间隔':6} 名称")
    for m in metas:
        if "error" in m:
            _p(f"{m['code']:16} 加载失败: {m['error']}")
            continue
        _p(f"{m['code']:16} {m['protection']:5} "
           f"{'需要' if m['requires_browser'] else '不需要':7} "
           f"{'支持' if m['supports_search'] else '不支持':5} "
           f"{m['suggested_interval']:<6} {m['name']}")
    return 0


def cmd_search(args) -> int:
    from .scenarios import s1_keyword_search
    config.ensure_dirs()
    with Database(args.db) as db:
        db.init_schema()
        st = s1_keyword_search(args.site, args.keyword, limit=args.limit,
                               pages=args.pages, want_reviews=not args.no_reviews,
                               db=db)
        _p(f"\n完成: {st.summary()}")
    return 0 if st.ok else 1


def cmd_detail(args) -> int:
    from .scenarios import s3_detail
    config.ensure_dirs()
    with Database(args.db) as db:
        db.init_schema()
        st = s3_detail(args.site, args.sku, want_reviews=not args.no_reviews, db=db)
        _p(f"\n完成: {st.summary()}")
    return 0 if st.ok else 1


def cmd_monitor(args) -> int:
    from .scenarios import s2_model_monitor, s2_monitor_known
    config.ensure_dirs()
    # --from-db：对已入库商品按 SKU 快照，定时任务用这个更可靠
    if args.from_db:
        with Database(args.db) as db:
            db.init_schema()
            st = s2_monitor_known(args.site, limit=args.limit, db=db)
            _p(f"\n完成: {st.summary()}")
        return 0 if st.ok else 1

    models = list(args.model or [])
    if args.models_file:
        with open(args.models_file, encoding="utf-8") as f:
            models += [ln.strip() for ln in f if ln.strip()
                       and not ln.startswith("#")]
    if not models:
        _p("请用 --model / --models-file 提供型号，或用 --from-db 监控已入库商品")
        return 2
    with Database(args.db) as db:
        db.init_schema()
        st = s2_model_monitor(args.site, models, db=db)
        _p(f"\n完成: {st.summary()}")
    return 0 if st.ok else 1


def cmd_reviews(args) -> int:
    from .scenarios import s4_review_incremental
    config.ensure_dirs()
    with Database(args.db) as db:
        db.init_schema()
        st = s4_review_incremental(args.site, args.sku, pages=args.pages, db=db)
        _p(f"\n完成: {st.summary()}")
    return 0 if st.ok else 1


def cmd_probe(args) -> int:
    from .scenarios import probe_selectors
    config.ensure_dirs()
    out = probe_selectors(args.site, args.url)
    _p(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if not out.get("blocked") and out.get("status") == 200 else 1


def cmd_products(args) -> int:
    with Database(args.db) as db:
        db.init_schema()
        rows = db.latest_products(args.site or "", args.limit)
        if not rows:
            _p("(无数据)")
            return 0
        _p(f"{'id':>4} {'site':12} {'sku':14} {'model':18} {'price':>10} "
           f"{'cur':4} {'评价':>4}  标题")
        for r in rows:
            _p(f"{r['id']:>4} {(r['site'] or '')[:12]:12} {(r['sku'] or '')[:14]:14} "
               f"{(r['model'] or '')[:18]:18} "
               f"{(r['last_price'] if r['last_price'] is not None else ''):>10} "
               f"{(r['currency'] or '')[:4]:4} {r['n_reviews']:>4}  "
               f"{(r['title'] or '')[:46]}")
    return 0


def cmd_history(args) -> int:
    with Database(args.db) as db:
        rows = db.price_history(args.product_id, args.limit)
        if not rows:
            _p("(无价格快照)")
            return 0
        _p(f"{'captured_at':22} {'price':>10} {'list':>10} {'cur':4} 有货")
        for r in rows:
            _p(f"{r['captured_at']:22} "
               f"{(r['price'] if r['price'] is not None else ''):>10} "
               f"{(r['list_price'] if r['list_price'] is not None else ''):>10} "
               f"{(r['currency'] or '')[:4]:4} "
               f"{'-' if r['in_stock'] is None else ('是' if r['in_stock'] else '否')}")
    return 0


def cmd_export(args) -> int:
    from . import export
    config.ensure_dirs()
    out = args.out
    if not out:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = "xlsx" if args.format == "xlsx" else "csv"
        out = os.path.join(config.DATA_DIR, f"网评汇总_{ts}.{ext}")
    with Database(args.db) as db:
        db.init_schema()
        if args.format == "xlsx":
            n = export.export_xlsx(db, out, site_code=args.site or "", limit=args.limit)
        else:
            n = export.export_csv(db, out, site_code=args.site or "", limit=args.limit)
    _p(f"已导出 {n} 行评价 → {out}")
    return 0


def cmd_spec_onboard(args) -> int:
    """发现阶段一站式：Probe(在线/离线) → LLM 生成 schema → 校验 → 保存。

    - 在线：--url + 浏览器探测（需 Playwright）；
    - 离线：--html-file 直接喂已渲染 HTML；
    数据源二选一。通过校验后把 AdapterSpec 写到 --out。
    """
    import json as _json
    from .llm_client import LLMClient
    from .spec_generator import generate_spec, prune_html
    from .spec_probe import build_probe_report, probe_site

    config.ensure_dirs()
    client = LLMClient()
    if not client.configured:
        _p("[FAIL] LLM 未配置：设置 OVERSEAS_LLM_PROVIDER / OVERSEAS_LLM_API_KEY / OVERSEAS_LLM_MODEL")
        return 2
    _p(f"[LLM] {client.describe()}")

    entry_html = ""
    report: dict = {}
    if args.html_file:
        from pathlib import Path as _Path
        p = _Path(args.html_file)
        if not p.exists():
            _p(f"[FAIL] HTML 文件不存在: {args.html_file}")
            return 1
        entry_html = p.read_text(encoding="utf-8", errors="replace")
        report = build_probe_report(
            args.url or "", entry_html, code=args.site,
            brand_name=args.brand_name or args.site, region=args.region or "",
            expected_model_count=args.expected_models, after_scroll_html=entry_html)
        _p(f"[PROBE] 离线 HTML {len(entry_html):,} 字符")
    elif args.url:
        from .fetchers import BrowserFetcher
        _p(f"[PROBE] 在线打开 {args.url}")
        with BrowserFetcher() as fetcher:
            report, entry_html = probe_site(
                fetcher, args.url, code=args.site,
                brand_name=args.brand_name or args.site, region=args.region or "",
                expected_model_count=args.expected_models,
                wait_selector="a[href]", scroll_passes=8)
        _p(f"[PROBE] 渲染 HTML {len(entry_html):,} 字符")
    else:
        _p("[FAIL] 需指定 --url 或 --html-file")
        return 1

    summary = report.get("entry_dom_summary", {})
    _p(f"[PROBE] best_selector={summary.get('best_link_selector')} "
       f"links={summary.get('static_unique_links')} "
       f"load_hint={summary.get('load_more_hint')}")

    # 分页站首屏 HTML 不含全量型号，找全率交给发现阶段判定；此处静态校验为主。
    is_paginate = summary.get("load_more_hint") in (
        "paginate", "paginate_replace", "click_more_or_paginate")
    spec, problems = generate_spec(
        report, client=client, max_repair=args.max_repair,
        entry_html="" if is_paginate else entry_html,
        score_threshold=args.threshold)
    _p(f"[GEN] discover={_json.dumps(spec.get('discover', {}), ensure_ascii=False)}")
    if problems:
        _p("[FAIL] 未通过校验/打分：")
        for pr in problems:
            _p(f"  - {pr}")
        if args.save_on_fail and spec:
            _dump_spec(spec, args.out, args.site)
            _p(f"[SAVE] 已保存未通过的候选 schema（--save-on-fail）")
        return 1

    # 通过校验后，可选：在线用引擎实际发现，核对找全率
    if args.url and args.verify_discover:
        from .fetchers import BrowserFetcher
        from .generic_spec import build_generic_adapter
        from .scenarios import open_dom
        adapter = build_generic_adapter(spec)
        with BrowserFetcher() as fetcher:
            def _open(u, wait_selector=""):
                return open_dom(adapter, fetcher, u, wait_selector=wait_selector)
            adapter.series_entries(_open)
        audit = adapter.last_entry_audit
        found = int(audit.get("discovered_model_count") or 0)
        _p(f"[DISCOVER] series={audit.get('discovered_series_count')} models={found}")
        if args.expected_models:
            cov = found / max(1, args.expected_models)
            _p(f"[COVERAGE] {found}/{args.expected_models} = {cov:.0%}")

    _dump_spec(spec, args.out, args.site)
    _p(f"[OK] schema 已保存 → {_spec_out_path(args.out, args.site)}")
    return 0


def _spec_out_path(out: str | None, site: str) -> str:
    if out:
        return out
    return os.path.join(config.DATA_DIR, f"schema_{site}.json")


def _dump_spec(spec: dict, out: str | None, site: str) -> None:
    import json as _json
    path = _spec_out_path(out, site)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(spec, f, ensure_ascii=False, indent=2)


def cmd_spec_crawl(args) -> int:
    from .scenarios import spec_crawl
    config.ensure_dirs()

    # --schema 非空：用 spec-onboard 产出的 AdapterSpec 构造通用适配器，
    # 打通「发现阶段生成 schema → 按 schema 实抓 SPEC」的第二阶段，
    # 无需在 SiteRegistry 预注册站点。
    adapter = None
    if getattr(args, "schema", None):
        import json as _json
        from pathlib import Path as _Path
        from .generic_spec import build_generic_adapter
        p = _Path(args.schema)
        if not p.exists():
            _p(f"[FAIL] schema 文件不存在: {args.schema}")
            return 1
        spec = _json.loads(p.read_text(encoding="utf-8"))
        adapter = build_generic_adapter(spec)
        _p(f"[SCHEMA] 由 {args.schema} 构造适配器 code={adapter.code}")

    with Database(args.db) as db:
        db.init_schema()
        st = spec_crawl(args.site, only_series=args.series, db=db, adapter=adapter)
        _p(f"\n完成: {st.summary()}")
    return 0 if st.ok and st.fail == 0 and not st.stopped else 1


def cmd_spec_export(args) -> int:
    from . import spec_export
    config.ensure_dirs()
    out = args.out
    if not out:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d")
        ext = "xlsx" if args.format == "xlsx" else "csv"
        out = os.path.join(config.DATA_DIR, f"SPEC_{args.site}_{ts}.{ext}")
    # 上市时间映射：显式 --release-map 优先，否则用 data/发售日_<brand>.json（存在才加载）。
    rel_path = args.release_map
    if not rel_path:
        default_rel = os.path.join(config.DATA_DIR, f"发售日_{args.site}.json")
        rel_path = default_rel if os.path.exists(default_rel) else None
    release_map = spec_export.load_release_map(rel_path)
    if release_map:
        _p(f"已加载上市时间映射: {rel_path}（{len(release_map)} 个型号）")
    with Database(args.db) as db:
        db.init_schema()
        if args.format == "xlsx":
            n = spec_export.export_xlsx(db, args.site, out, release_map)
        else:
            n = spec_export.export_csv(db, args.site, out, release_map)
    _p(f"已导出 {n} 行 SPEC → {out}")
    return 0


def cmd_spec_dict(args) -> int:
    """列出词典未命中的项目，便于补词典。"""
    with Database(args.db) as db:
        db.init_schema()
        rows = db.untranslated_list()
        if not rows:
            _p("(无未翻译项目)")
            return 0
        _p(f"{'次数':>5}  {'品牌':14} 日文项目")
        for r in rows:
            _p(f"{r['seen_count']:>5}  {(r['brand'] or '')[:14]:14} {r['item_ja']}")
    return 0


def cmd_stats(args) -> int:
    with Database(args.db) as db:
        db.init_schema()
        for k, v in db.stats().items():
            _p(f"  {k:18} = {v}")
    return 0


def cmd_cache_clear(args) -> int:
    from .infra import DiskCache
    n = DiskCache().clear()
    _p(f"已清理缓存文件 {n} 个")
    return 0


def cmd_login(args) -> int:
    """打开有头浏览器，人工完成登录后把 storage_state 落到 data/session/。

    退出码：0 已保存登录态；1 未检测到登录 / 未保存。
    """
    import time as _time

    from .infra import login_wall_hit
    from .fetchers import BrowserFetcher

    config.ensure_dirs()
    site = (args.site or "").strip()
    if not site:
        _p("需要 --site <站点code>，例如 --site amazon_ca")
        return 1

    try:
        adapter = SiteRegistry.get(site)
    except Exception as e:
        _p(f"未知站点 {site}: {type(e).__name__}: {e}")
        return 1

    start_url = args.url or getattr(adapter, "base_url", "") or ""
    if not start_url:
        _p(f"站点 {site} 没有 base_url，请显式传 --url")
        return 1

    state_path = config.session_path(site)
    timeout = max(30, int(args.timeout or 600))
    interval = max(2, int(getattr(args, "interval", 5) or 5))
    keep_open = bool(getattr(args, "keep_open", False))

    _p(f"站点      : {site} ({getattr(adapter, 'name', '')})")
    _p(f"起始 URL  : {start_url}")
    _p(f"登录态落盘: {state_path}")
    _p(f"等待上限  : {timeout}s（每 {interval}s 自动检测一次）")
    _p(f"完毕后窗口: {'保留打开（--keep-open）' if keep_open else '自动关闭'}")
    _p("")
    _p(">>> 即将打开浏览器窗口，请在其中完成登录（含验证码/两步验证）。")
    _p(">>> 登录成功后会自动保存登录态；窗口只会在这之后关闭。")
    _p("")

    # 有头模式：必须显式 headless=False；persist_state=True 保证检测通过后能落盘
    use_profile = bool(getattr(args, "profile", False))
    if use_profile:
        pdir = config.profile_dir(site)
        _p(f"浏览器 profile: {pdir}")
        _p("    （持久化真实用户目录，反爬更信任；登录态保存在 profile 里）")
        _p("")
        bf = BrowserFetcher(headless=False, site_code=site,
                            persist_state=True, profile_dir=pdir)
    else:
        bf = BrowserFetcher(headless=False, site_code=site, persist_state=True)
    ok = False
    try:
        if args.interactive:
            # 人工回车路径：只开一个页面，回车后才另开一次校验（唯一的一次导航）
            from .infra import login_wall_hit
            _p("[*] 请在弹出的窗口里登录，完成后回本终端按回车。")
            with bf.page(start_url, wait_selector="", settle_ms=1500,
                         scroll_passes=0) as (res, _dom):
                fin = str(res.meta.get("final_url") or res.url)
                _p(f"[i] 当前页：{res.title!r} @ {fin[:70]}")
                try:
                    input("登录完成后按回车继续... ")
                except (EOFError, KeyboardInterrupt):
                    _p("[i] 未收到回车，转为被动轮询（不做任何校验导航）。")
                    ok = bf.wait_for_login(start_url, timeout=timeout,
                                           interval=interval)
                else:
                    probe = str(getattr(adapter, "login_probe_url", "") or "")
                    ok = bf._verify_logged_in(start_url, verbose=True,
                                              probe_url=probe)
                    if ok:
                        _p("[✓] 校验通过（需登录页可访问）。")
                    else:
                        _p("[!] 校验未通过：需登录页仍跳登录。")
        else:
            _p("[*] 开始被动检测登录状态（请在弹出的浏览器里登录）...")
            ok = bf.wait_for_login(start_url, timeout=timeout,
                                   interval=interval)
            if ok:
                _p("[✓] 检测到已登录。")
            else:
                _p("[!] 在等待上限内未检测到登录。")
                if not args.force:
                    _p("    未保存。确认已登录可加 --force 强制保存，"
                       "或用 --timeout 加长等待。")
    except Exception as e:
        _p(f"[!] 出错: {type(e).__name__}: {str(e)[:200]}")
        if not args.force:
            return 1
    finally:
        # 校验通过后强制落盘；save_state 内置"登录墙不回写"的防污染规则
        saved = bf.save_state(force=bool(ok or args.force))

        # 报告实际拿到的凭证类型：只拿到 session-token 说明"有会话但未真正登录"，
        # 这正是上一轮被误判为"已登录"的原因，必须显式告知。
        auth_names: list[str] = []
        try:
            cks = bf._ctx.cookies(adapter.base_url + "/") if bf._ctx else []
            # 凭证 cookie 名带国别后缀（ca→at-acbca / us→at-main / mx→at-acbmx），
            # 必须用前缀匹配，硬编码名字会漏报（曾因此误判"未登录"）。
            auth_names = sorted(
                str(c.get("name")) for c in cks
                if str(c.get("name")).startswith(("at-", "sess-at-", "x-"))
                and not str(c.get("name")).startswith(("at-a-glance", "x-amz")))
            auth_names += sorted(
                str(c.get("name")) for c in cks
                if str(c.get("name")) in ("session-token", "session-id"))
        except Exception:
            pass
        strong = any(n.startswith(("at-", "sess-at-", "x-"))
                     and not n.startswith(("at-a-glance", "x-amz"))
                     for n in auth_names)

        _p("")
        _p(f"    凭证 cookie: {', '.join(auth_names) or '(无)'}")
        if auth_names:
            if strong:
                _p("    ✓ 含持久登录凭证（at-main/x-main）——已真正登录")
            else:
                _p("    ⚠ 只拿到 session-token（匿名访客也有）——很可能未真正登录；")
                _p("      重跑本命令，确认在浏览器里完成登录并看到账号名/Your Account")

        if saved or os.path.exists(state_path):
            size = os.path.getsize(state_path) if os.path.exists(state_path) else 0
            _p("")
            _p(f"[✓] 登录态已保存: {state_path} ({size} 字节)")
            _p("    注意：该文件含 cookie，属敏感凭据，勿提交到版本库。")
            _p(f"    后续抓取 {site} 会自动复用；失效后重跑本命令即可。")
        else:
            _p("")
            _p("[!] 未写入登录态文件（未检测到登录或校验未通过）。")

        # --keep-open：先让用户看清窗口，再关闭。避免"刚登录完窗口就消失"。
        if keep_open and bf._ctx is not None:
            hold = max(5, int(getattr(args, "keep_open_seconds", 120) or 120))
            _p("")
            _p(f"[*] 保持窗口打开 {hold}s（--keep-open）。"
               f"可直接在窗口里核对登录状态；提前关闭窗口即结束。")
            deadline_hold = _time.time() + hold
            while _time.time() < deadline_hold:
                try:
                    alive = [p for p in bf._ctx.pages if not p.is_closed()]
                    if not alive:
                        _p("    窗口已被关闭。")
                        break
                except Exception:
                    break
                _time.sleep(2)
            _p("    关闭浏览器。")

        bf.close(save_state=False)
        _time.sleep(0.1)
    return 0 if (ok or args.force) else 1


def cmd_login_status(args) -> int:
    """查看各站点已保存的登录态及其新鲜度。"""
    config.ensure_dirs()
    import time as _time
    d = config.SESSION_DIR
    files = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".state.json"):
                p = os.path.join(d, fn)
                try:
                    st = os.stat(p)
                    with open(p, encoding="utf-8") as fh:
                        data = json.load(fh)
                    nc = len(data.get("cookies") or [])
                    no = len(data.get("origins") or [])
                except Exception:
                    st, nc, no = None, -1, -1
                files.append((fn[:-len(".state.json")], p, st, nc, no))
    if not files:
        _p(f"暂无已保存登录态（目录 {d}）")
        return 0
    _p(f"登录态目录: {d}")
    _p(f"{'站点':<20}{'cookie':>7}{'origin':>8}  最后更新")
    for code, _p_, st, nc, no in files:
        when = (_time.strftime("%Y-%m-%d %H:%M", _time.localtime(st.st_mtime))
                if st else "?")
        _p(f"{code:<20}{nc:>7}{no:>8}  {when}")
    return 0


def cmd_retail_health(args) -> int:
    """零售站小样健康探测（P1-2）。退出码：0 全健康；1 有骤降/失败项。"""
    from .retail_health import check_repo, main as _health_main
    # 直接复用 retail_health.main 的 CLI 逻辑（避免双份 argparse 冲突）
    real = args.__dict__.copy()
    del real["func"]
    # 把已解析的 arg 对象序列化回命令行后再跑；更简单：直接调用其函数。
    if args.all:
        from .retail_health import check_repo
        reports = check_repo(None, args.models_file, args.max_models,
                             not args.no_reviews, True)
    elif args.spec:
        from .retail_spec import load_retail_spec
        from .retail_health import run_health, _load_models
        spec = load_retail_spec(args.spec)
        models = _load_models(args.models_file, args.models)
        if not models and args.db:
            # 无型号输入时从库读该站历史型号（健康探测的正确样本：契约是否还贴页面）
            try:
                from . import config as _cfg
                from .db import Database
                db = Database(args.db or _cfg.DB_PATH)
                db.init_schema()
                sids = [r["id"] for r in db.conn.execute(
                    "SELECT id FROM site WHERE code=?", (spec["code"],)).fetchall()]
                if sids:
                    sid = sids[0]
                    rows = db.conn.execute(
                        "SELECT model, brand FROM product WHERE site_id=? "
                        "ORDER BY id DESC LIMIT 20", (sid,)).fetchall()
                    models = [{"model_raw": r["model"], "brand_code": "",
                               "brand_name": r["brand"] or ""} for r in rows]
                db.close()
            except Exception:
                pass
        reports = [run_health(spec, models, max_models=args.max_models,
                              want_reviews=not args.no_reviews,
                              verbose=True)]
    else:
        _p("需要 --spec <路径> 或 --all")
        return 2

    if args.json:
        import json as _json
        _p(_json.dumps([r.to_dict() for r in reports],
                       ensure_ascii=False, indent=2))
    else:
        for r in reports:
            if r.models_checked:
                _p(f"[{'PASS' if r.ok else 'FAIL'}] {r.code}: "
                   f"checked={r.models_checked} matched={r.models_matched} "
                   f"price_fill={r.price_fill_rate:.0%} reviews={r.review_count}")
                for prob in r.problems:
                    _p(f"      - {prob}")
            else:
                _p(f"[{'PASS' if r.ok else 'FAIL'}] {r.code}: {r.problems}")
    return 0 if all(r.ok for r in reports) else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="overseas", description="海外电商采集框架")
    ap.add_argument("--db", default=None, help="数据库路径（默认见 config）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("config", help="显示当前配置").set_defaults(func=cmd_config)
    sub.add_parser("init-db", help="初始化数据库").set_defaults(func=cmd_init_db)

    s = sub.add_parser("sites", help="列出已注册站点")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sites)

    s = sub.add_parser("search", help="S1 关键词搜索采集")
    s.add_argument("--site", required=True)
    s.add_argument("--keyword", required=True)
    s.add_argument("--limit", type=int, default=5, help="最多采集商品数")
    s.add_argument("--pages", type=int, default=1, help="搜索翻页数")
    s.add_argument("--no-reviews", action="store_true", help="不采评价")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("detail", help="S3 详情直采（sku 或完整 URL）")
    s.add_argument("--site", required=True)
    s.add_argument("--sku", action="append", required=True)
    s.add_argument("--no-reviews", action="store_true")
    s.set_defaults(func=cmd_detail)

    s = sub.add_parser("monitor", help="S2 价格监控（按型号搜索，或 --from-db 按已入库 SKU）")
    s.add_argument("--site", required=True)
    s.add_argument("--model", action="append")
    s.add_argument("--models-file")
    s.add_argument("--from-db", action="store_true",
                   help="对已入库商品按 SKU 做价格快照（推荐用于定时任务）")
    s.add_argument("--limit", type=int, default=200, help="--from-db 时最多监控多少个商品")
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("reviews", help="S4 评价增量追踪")
    s.add_argument("--site", required=True)
    s.add_argument("--sku", action="append", required=True)
    s.add_argument("--pages", type=int, default=3)
    s.set_defaults(func=cmd_reviews)

    s = sub.add_parser("probe", help="选择器自检（DOM 改版时先跑这个）")
    s.add_argument("--site", required=True)
    s.add_argument("--url", required=True)
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("products", help="查看已采集商品")
    s.add_argument("--site")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_products)

    s = sub.add_parser("history", help="查看某商品价格快照历史")
    s.add_argument("--product-id", type=int, required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("export", help="按目标表格格式导出（每条评价一行）")
    s.add_argument("--site", help="只导某站点，缺省导全部")
    s.add_argument("--format", choices=["csv", "xlsx"], default="csv")
    s.add_argument("--out", help="输出路径，缺省写到 data/ 下带时间戳")
    s.add_argument("--limit", type=int, default=100000)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("spec-crawl", help="SPEC 采集（规格表，周度）")
    s.add_argument("--site", help="已注册站点代号，如 hisense_jp；用 --schema 时可省略")
    s.add_argument("--series", action="append", help="只抓指定系列（调试），可多次")
    s.add_argument("--schema", help="spec-onboard 产出的 schema JSON；提供后按该 schema "
                   "构造通用适配器实抓，无需在 SiteRegistry 注册")
    s.set_defaults(func=cmd_spec_crawl)

    s = sub.add_parser("spec-export", help="按品牌导出 SPEC 表（区分|项目|中文|机型...）")
    s.add_argument("--site", required=True, help="品牌 code，如 hisense_jp")
    s.add_argument("--format", choices=["csv", "xlsx"], default="xlsx")
    s.add_argument("--out", help="输出路径，缺省写到 data/")
    s.add_argument("--release-map", help="型号→上市时间 JSON（缺省用 data/发售日_<brand>.json）；"
                   "提供后在 SPEC 表顶部增加「上市时间」行")
    s.set_defaults(func=cmd_spec_export)

    s = sub.add_parser("spec-onboard",
                       help="发现阶段：探测陌生站→LLM 生成 schema→校验→保存")
    s.add_argument("--site", required=True, help="站点代号，如 sony_uk")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="入口 URL（在线浏览器探测，需 Playwright）")
    src.add_argument("--html-file", help="本地已渲染 HTML（离线探测）")
    s.add_argument("--brand-name", help="展示名，缺省用 site")
    s.add_argument("--region", help="区域码，如 ca/us/uk")
    s.add_argument("--expected-models", type=int, help="官网标称型号数（用于找全率校验）")
    s.add_argument("--threshold", type=float, default=70.0, help="实测打分通过线（默认 70）")
    s.add_argument("--max-repair", type=int, default=3, help="LLM 修复轮次上限（默认 3）")
    s.add_argument("--out", help="schema 输出路径，缺省 data/schema_<site>.json")
    s.add_argument("--verify-discover", action="store_true",
                   help="通过校验后再在线用引擎实际发现型号核对找全率（需 --url）")
    s.add_argument("--save-on-fail", action="store_true",
                   help="未通过校验也保存候选 schema 供人工修")
    s.set_defaults(func=cmd_spec_onboard)

    s = sub.add_parser("spec-dict", help="查看词典未命中的日文项目（待补词典）")
    s.set_defaults(func=cmd_spec_dict)

    s = sub.add_parser("retail-health", help="零售站小样健康探测（P1-2，选择器骤降告警）")
    s.add_argument("--spec", help="RetailSpec JSON 路径（单站探测）")
    s.add_argument("--all", action="store_true",
                   help="遍历 docs/retail_specs/ 全部站（单站失败不阻塞）")
    s.add_argument("--models-file", help="型号清单 CSV（含 model_raw/brand_code 列）")
    s.add_argument("--models", nargs="*", help="直接给型号")
    s.add_argument("--max-models", type=int, default=3, help="每站小样数")
    s.add_argument("--no-reviews", action="store_true",
                   help="只验证价格选择器（不翻评价页）")
    s.add_argument("--json", action="store_true", help="输出 JSON（供调度解析）")
    s.set_defaults(func=cmd_retail_health)

    sub.add_parser("stats", help="数据库统计").set_defaults(func=cmd_stats)
    sub.add_parser("cache-clear", help="清理磁盘缓存").set_defaults(func=cmd_cache_clear)

    s = sub.add_parser("login",
                       help="打开有头浏览器人工登录，保存登录态供后续抓取复用")
    s.add_argument("--site", required=True, help="站点 code，如 amazon_ca")
    s.add_argument("--url", default="", help="起始 URL（默认用站点 base_url）")
    s.add_argument("--timeout", type=int, default=600,
                   help="等待登录的最长秒数（默认 600）")
    s.add_argument("--interval", type=int, default=5,
                   help="登录状态轮询间隔秒数（默认 5）")
    s.add_argument("--keep-open", action="store_true",
                   help="保存登录态后保留浏览器窗口一段时间，便于核对")
    s.add_argument("--keep-open-seconds", type=int, default=120,
                   help="--keep-open 的保留秒数（默认 120）")
    s.add_argument("--interactive", action="store_true",
                   help="改为人工回车确认（默认自动轮询检测）")
    s.add_argument("--profile", action="store_true",
                   help="用持久化浏览器 profile 登录（推荐：反爬更信任，登录更稳）")
    s.add_argument("--force", action="store_true",
                   help="即使未检测到登录也强制保存当前会话")
    s.set_defaults(func=cmd_login)

    sub.add_parser("login-status",
                   help="查看各站点已保存登录态及新鲜度").set_defaults(
        func=cmd_login_status)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _p("\n已中断")
        return 130
    except Exception as e:
        _p(f"错误: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
