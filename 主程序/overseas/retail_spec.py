"""零售线契约：RetailSpec 加载与校验 + 配置化型号匹配与多店铺抽取。

对齐零售线方案（docs/零售线多国自动适配技术方案.md）与任务表 P0-1~P0-4：
    P0-1  RetailSpec 加载 + L0~L3 静态校验 + 白名单常量
    P0-2  shops 段多店铺抽取（single_shop / multi_shop + lowest + 列映射）
    P0-4  match 段配置化型号匹配（三段兜底 + reject + on_no_match=skip）

设计原则（继承 SPEC 线）：
- **只产配置，不产代码**：RetailSpec 是纯 JSON，执行端白名单化。
- **能力边界显式化**：枚举值必须在白名单内，越界报错而非静默降级。
- **宁缺毋滥**：`on_no_match` 必须 `skip`，型号匹配宁可不记也不能张冠李戴。

与 `overseas/sites/<code>/schema.json` 的关系：
    RetailSpec 是现有六段式（anchor/search/product/price/specs/summary/reviews
    由 Extractor 执行）的规范化 + 扩展，新增 match/paginate/incremental/shops/export 段。
    本模块只处理「新段」，存量六段仍由 extract.py 的 load_schema/Extractor 执行。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

# ---------------------------------------------------------------- 白名单常量

# 对齐任务表 P0-1 子任务 1.3。新增枚举值必须同步：
#   1) 本白名单；2) docs/RetailSpec_schema规范.md §12；3) 校验示例（如有）。
SUPPORTED_MATCH_STRATEGIES = {"search_then_verify", "url_template", "sku_map"}
SUPPORTED_REVIEW_PAGINATE = {"url_page", "query_param", "click_more", "none",
                             "filter_sweep",
                             # bv_api：评价不在商品页 DOM 里，走 BazaarVoice
                             # 客户端 JSON 接口分页（见 overseas/bv_reviews.py）。
                             # 由 crawl_ca_retail 的 _collect_reviews 分派，
                             # 不进 collect_reviews_paged。
                             "bv_api"}
SUPPORTED_SHOP_MODES = {"multi_shop", "single_shop"}

# capabilities → 必须有段（L2 语义）
_CAPABILITY_REQUIRED_SECTIONS = {
    "price": ("price",),
    "reviews": ("reviews",),
}

_SAME_SCHEMES = {"http", "https"}

# ── R1-5 评价条数上限（可配置，不写死）─────────────────────────────
# 优先级：CLI 参数 > spec.paginate.reviews.max_reviews > 环境变量
# OVERSEAS_MAX_REVIEWS > 本默认值。现阶段默认 50（单型号单站最多 50 条），
# 后续调这里或用上面任一方式覆盖。
DEFAULT_MAX_REVIEWS = 50
# 翻页安全阀：防站点对越界页号仍返回首页导致的死循环
DEFAULT_MAX_REVIEW_PAGES = 10


def resolve_max_reviews(spec: dict | None = None,
                        cli_value: int | None = None) -> int:
    """解析评价条数上限。cli > spec > 环境变量 > 默认值（50）。

    返回 0 表示不抓评价；负数按 0 处理。
    """
    if cli_value is not None and int(cli_value) >= 0:
        return int(cli_value)
    page_cfg = ((spec or {}).get("paginate") or {}).get("reviews") or {}
    if page_cfg.get("max_reviews") is not None:
        try:
            v = int(page_cfg["max_reviews"])
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    env = os.environ.get("OVERSEAS_MAX_REVIEWS", "").strip()
    if env:
        try:
            v = int(env)
            if v >= 0:
                return v
        except ValueError:
            pass
    return DEFAULT_MAX_REVIEWS


def resolve_max_review_pages(spec: dict | None = None,
                             cli_value: int | None = None) -> int:
    """解析评价翻页页数安全阀。cli > spec > 默认值（10）。"""
    if cli_value is not None and int(cli_value) > 0:
        return int(cli_value)
    page_cfg = ((spec or {}).get("paginate") or {}).get("reviews") or {}
    if page_cfg.get("max_pages") is not None:
        try:
            v = int(page_cfg["max_pages"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_REVIEW_PAGES


# ---------------------------------------------------------------- 加载

def load_retail_spec(path) -> dict:
    """从 JSON 文件加载 RetailSpec。文件不存在或解析失败抛 ValueError。"""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"RetailSpec 文件不存在: {p}")
    with p.open(encoding="utf-8") as f:
        try:
            spec = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"RetailSpec 不是合法 JSON: {p}: {e}") from e
    if not isinstance(spec, dict):
        raise ValueError(f"RetailSpec 顶层必须是对象: {p}")
    return spec


# ---------------------------------------------------------------- L0~L3 校验

def validate_retail_spec(spec: dict) -> list[str]:
    """静态校验 RetailSpec，返回问题列表（空 = 通过）。

    四层（对齐 RetailSpec_schema规范.md §11）：
      L0 结构   必填字段齐；所有选择器可解析；正则可编译
      L1 安全   所有 URL 与 base_url 同源（防 SSRF）；不含可执行代码
      L2 语义   capabilities 含 price 时 price 段必填；含 reviews 时 reviews 段必填
      L3 红线   on_no_match 必须 skip；match.verify.require_model_in 非空
    """
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["RetailSpec 顶层必须是对象"]

    # ---- L0 结构 ----
    code = spec.get("code")
    base_url = spec.get("base_url")
    if not code or not isinstance(code, str):
        problems.append("缺少必填字段 code（str）")
    if not base_url or not isinstance(base_url, str):
        problems.append("缺少必填字段 base_url（str）")
    caps = spec.get("capabilities")
    # capabilities 必须存在且为 list[str]，但**允许空列表**：封锁站占位 spec
    # 声明 capabilities=[] 表示该站当前无任何可抓能力（status=blocked），属合法
    # 状态。空列表时 L2/L3 的 price/match 检查自然跳过（无能力就无需匹配规则）。
    if caps is None or not isinstance(caps, list) \
            or not all(isinstance(c, str) for c in caps):
        problems.append("缺少必填字段 capabilities（list[str]）")

    _check_required_section_types(spec, problems)
    _check_selectors_parseable(spec, problems)
    _check_regex_compile(spec, problems)

    # ---- L1 安全 ----
    _check_urls_same_origin(spec, problems)

    # ---- L2 语义 ----
    caps_set = set(caps) if caps else set()
    for cap in caps_set:
        for sec in _CAPABILITY_REQUIRED_SECTIONS.get(cap, ()):
            if sec not in spec:
                problems.append(f"capabilities 含 {cap!r}，但没有 {sec} 段")
    rv_cfg = (spec.get("reviews") or {}).get("fields") or {}
    if "reviews" in caps_set and not rv_cfg.get("review_key"):
        problems.append("capabilities 含 'reviews' 时 reviews.fields.review_key 必填"
                        "（评价增量去重的唯一键）")

    # ---- L3 红线 ----
    m = spec.get("match") or {}
    # 只要声明了抓取能力就必须有 match 段：否则"无 match 段"可绕过 on_no_match
    # 红线，等于放开张冠李戴的口子。
    caps_l3 = spec.get("capabilities") or []
    if not m and any(c in ("price", "reviews") for c in caps_l3):
        problems.append("缺 match 段：声明了 price/reviews 能力就必须给出型号匹配规则"
                        "（含 on_no_match=skip），否则无法防止型号张冠李戴")
    if m:
        on_no_match = m.get("on_no_match")
        if on_no_match != "skip":
            problems.append(
                f"match.on_no_match 必须是 'skip'（宁缺毋滥），当前是 {on_no_match!r}；"
                "禁止用 first 提高覆盖率（会污染数据）")
        verify = m.get("verify") or {}
        if not verify.get("require_model_in"):
            problems.append("match.verify.require_model_in 不能为空"
                            "（型号必须校验出现在哪些字段）")
        strategy = m.get("strategy")
        if strategy and strategy not in SUPPORTED_MATCH_STRATEGIES:
            problems.append(
                f"match.strategy 不支持: {strategy!r}（可用: {sorted(SUPPORTED_MATCH_STRATEGIES)}）")
        reject = m.get("reject") or {}
        for key in ("title_contains_any", "url_contains_any"):
            if key in reject and not isinstance(reject[key], list):
                problems.append(f"match.reject.{key} 必须是 list")

    # ---- 白名单枚举复核（paginate / shops / fetch）----
    page = (spec.get("paginate") or {}).get("reviews") or {}
    ptype = page.get("type")
    if ptype and ptype not in SUPPORTED_REVIEW_PAGINATE:
        problems.append(
            f"paginate.reviews.type 不支持: {ptype!r}（白名单: {sorted(SUPPORTED_REVIEW_PAGINATE)}）")
    shops = spec.get("shops") or {}
    smode = shops.get("mode")
    if smode and smode not in SUPPORTED_SHOP_MODES:
        problems.append(
            f"shops.mode 不支持: {smode!r}（白名单: {sorted(SUPPORTED_SHOP_MODES)}）")
    if smode == "multi_shop":
        if not shops.get("list") or not isinstance(shops.get("list"), dict):
            problems.append("shops.mode=multi_shop 时必须有 shops.list（dict）")
        if not shops.get("columns") or not isinstance(shops.get("columns"), list):
            problems.append("shops.mode=multi_shop 时必须有 shops.columns（list）")

    return problems


def _check_required_section_types(spec: dict, problems: list[str]) -> None:
    """L0：新段类型检查（存量六段交给 Extractor 运行时兜底）。"""
    required_type = {
        "match": dict, "paginate": dict, "incremental": dict,
        "shops": dict, "export": dict, "fetch": dict, "expected": dict,
    }
    for sec, t in required_type.items():
        v = spec.get(sec)
        if v is not None and not isinstance(v, t):
            problems.append(f"{sec} 段必须是 {t.__name__}")


def _collect_selectors(spec: dict) -> list[tuple[str, str]]:
    """收集所有选择器字段，返回 [(路径, 选择器)]。遍历整棵 schema 树。"""
    out: list[tuple[str, str]] = []

    def walk(obj: Any, prefix: str) -> None:
        if isinstance(obj, dict):
            for k, child in obj.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                if k == "selectors" and isinstance(child, list):
                    for i, sel in enumerate(child):
                        if isinstance(sel, str) and sel:
                            out.append((f"{path}[{i}]", sel))
                elif k == "selectors" and isinstance(child, str) and child:
                    out.append((path, child))
                elif k == "container" and isinstance(child, str) and child:
                    out.append((path, child))
                elif k in ("key", "value") and isinstance(child, str) and child:
                    out.append((path, child))
                else:
                    walk(child, path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, f"{prefix}[{i}]")

    walk(spec, "spec")
    return out


def _check_selectors_parseable(spec: dict, problems: list[str]) -> None:
    """L0：所有 CSS 选择器用 lxml.cssselect 试解析（语法合法 ≠ 页面命中）。

    Playwright 专属伪类（:has() / :has-text() / :text() 等）是浏览器引擎专供，
    lxml 不认但运行时（BrowserFetcher→PlaywrightDom）有效——跳过这些，不算问题。
    """
    try:
        from lxml import cssselect
    except ImportError:
        return     # 无 lxml 时跳过；真实抓取会由 Extractor 兜底
    _PLAYWRIGHT_PSEUDO = re.compile(r":(?:has|has-text|contains|is|not)\s*[\(\[]")
    for path, sel in _collect_selectors(spec):
        if _PLAYWRIGHT_PSEUDO.search(sel):
            continue
        try:
            cssselect.CSSSelector(sel)
        except Exception as e:
            problems.append(f"选择器不可解析 {path}: {sel!r} → {type(e).__name__}: {e}")


def _check_regex_compile(spec: dict, problems: list[str]) -> None:
    """L0：所有带 regex 键的字段值必须能编译正则。"""
    def walk(v: Any, path: str) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                walk(child, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                walk(item, f"{path}[{i}]")
        elif isinstance(v, str) and path.endswith(".regex"):
            try:
                re.compile(v)
            except re.error as e:
                problems.append(f"正则不可编译 {path}: {v!r} → {e}")
    walk(spec, "")


# ---------------------------------------------------------------- L1: URL 同源

def _url_origin(url: str) -> tuple[str, str] | None:
    """取 URL 的 (scheme, host)。相对/协议相对/非法返回 None。"""
    if not url or not isinstance(url, str):
        return None
    m = re.match(r"^([a-z][a-z0-9+.-]*):\/\/([^/?#]+)", url.strip(), re.I)
    if not m:
        return None
    scheme = m.group(1).lower()
    if scheme not in _SAME_SCHEMES:
        return None
    netloc = m.group(2).lower()
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]       # 去 userinfo
    host = netloc
    # 去默认端口：host:port → host（80/443 等价）
    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if not port.isdigit():
            host = netloc
    return scheme, host


def _url_is_same_origin(url: str, spec: dict) -> bool:
    """url 与 spec.base_url 同源，或为相对/协议相对路径（运行时按 base 补全）。"""
    origin = _url_origin(url)
    if origin is None:
        return True      # 相对路径，放行
    base_origin = _url_origin(str(spec.get("base_url") or ""))
    if base_origin is None:
        return True      # base_url 非法由 L0 负责
    return origin == base_origin


def _collect_urls(spec: dict) -> list[str]:
    """收集 spec 里所有 URL 字段值。"""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                if k in ("url", "url_template", "entry_url", "base_url") \
                        and isinstance(child, str) and child:
                    out.append(child)
                elif k == "urls" and isinstance(child, list):
                    out.extend(u for u in child if isinstance(u, str))
                else:
                    walk(child)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(spec)
    return out


def _check_urls_same_origin(spec: dict, problems: list[str]) -> None:
    """L1：所有 URL 必须与 base_url 同源（防 SSRF）。"""
    base_url = spec.get("base_url") or ""
    base_origin = _url_origin(base_url)
    if base_origin is None:
        return     # base_url 缺失/非法由 L0 负责
    for u in _collect_urls(spec):
        if not _url_for_same_origin(u, base_origin):
            problems.append(f"URL 与 base_url 不同源（防 SSRF）: {u!r}（base={base_url}）")


def _url_for_same_origin(u: str, base_origin: tuple[str, str]) -> bool:
    origin = _url_origin(u)
    if origin is None:
        return True      # 相对路径，运行时按 base 补全
    return origin == base_origin


# ---------------------------------------------------------------- P0-4: match

def normalize_model(s: str) -> str:
    """型号归一化：去空格/连字符/下划线并大写（与 scenarios._norm_model 一致）。"""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def core_model(norm: str, min_len: int = 4, strip_leading_size: bool = True,
               strip_suffix: Iterable[str] = ()) -> str:
    """型号主干：去前导尺寸与尾缀（55QD8SF-PRO → QD8SF）。"""
    core = re.sub(r"^\d{2,3}", "", norm) if strip_leading_size else norm
    for suf in strip_suffix or ():
        if core.lower().endswith(str(suf).lower()):
            core = core[: -len(str(suf))]
    return core


def _leading_size(norm: str) -> str:
    """取型号前缀的尺寸数字（75U6SV → 75），无则空。"""
    m = re.match(r"(\d{2,3})", norm or "")
    return m.group(1) if m else ""


def _candidate_sizes(text: str, core: str, size_tokens: Iterable[str]) -> set[str]:
    """抽候选文本里「与 core 同款」的尺寸集合（含分隔词写法）。

    两类写法都认：
      1) 紧贴式：`75U6SV` / `75-u6sv`（归一化后 `75` + core）
      2) 分隔式：`65" Class - U7SG Series` / `65 inch U7SG` / `65 Class U7SG`
         —— 尺寸和 core 之间夹着 spec 声明的 `core_size_tokens`（class/inch/"/…）

    返回空集表示「这篇候选没标尺寸」，此时不做尺寸约束（与旧行为一致）。
    """
    ntext = normalize_model(text or "")
    if not ntext or not core:
        return set()
    sizes = set(re.findall(r"(\d{2,3})" + re.escape(core), ntext))
    tokens = [str(t) for t in (size_tokens or ()) if str(t)]
    if not tokens:
        return sizes
    # 分隔式：在原文里按「数字 + 分隔词 + … + core」匹配。core 可能跨标点，
    # 所以先把原文归一化成小写字母数字，分隔词也归一化后拼进正则。
    flat = re.sub(r"[^0-9a-z]+", " ", (text or "").lower())
    for token in tokens:
        tok = re.sub(r"[^0-9a-z]+", "", token.lower())
        if not tok:
            continue
        # 尺寸与 core 之间最多隔 3 个词（Class / - / Series 之类）
        for m in re.finditer(
                r"\b(\d{2,3})\b(?:\s+[0-9a-z]+){0,3}?\s+" + re.escape(core.lower()) + r"\b",
                flat):
            sizes.add(m.group(1))
    # 兜底：尺寸后直接跟 token（如 `65classu7sg`，无空格）
    for token in tokens:
        tok = re.sub(r"[^0-9a-z]+", "", token.lower())
        if tok:
            sizes.update(re.findall(r"(\d{2,3})" + re.escape(tok) + r"\w{0,12}?" + re.escape(core.lower()),
                                    ntext.lower()))
    return sizes


def explain_no_match(spec: dict, candidates: Iterable[Any], brand: str = "",
                     model: str = "") -> str:
    """`match_product` 返回 None 时，给出**可核对**的原因（便于分辨"没上架"与"规则太紧"）。

    这不是判断逻辑，只是把命中失败拆成三类，让人一眼知道下一步该查什么：
      - 候选为空                        → 搜索页没出结果（可能被拦/需等渲染）
      - 该系列一个都没有                → 该站确实没上架这个系列
      - 有同系列但尺寸不符              → 站上有这个系列，只是没有目标尺寸
      - 候选全被 reject 规则排除        → 规则误伤了，需要改 spec
    """
    m = spec.get("match") or {}
    verify = m.get("verify") or {}
    reject = m.get("reject") or {}
    want = normalize_model(model)
    if not want:
        return "型号为空"
    items = list(candidates or [])
    if not items:
        return "搜索页无候选"
    t_rejects = [str(x).lower() for x in (reject.get("title_contains_any") or [])]
    u_rejects = [str(x).lower() for x in (reject.get("url_contains_any") or [])]

    def rejected(c: Any) -> bool:
        title = str(getattr(c, "title", "") or "").lower()
        url = str(getattr(c, "url", "") or getattr(c, "sku", "") or "").lower()
        return any(t in title for t in t_rejects) or any(u in url for u in u_rejects)

    core = core_from_spec(verify, want)
    size_tokens = verify.get("core_size_tokens") or []
    target_size = _leading_size(want)
    core_seen: list[str] = []
    sizes: set[str] = set()
    for c in items:
        if rejected(c):
            continue
        text = f"{getattr(c, 'sku', '') or ''} {getattr(c, 'title', '') or ''} {getattr(c, 'url', '') or ''}"
        if core and core in normalize_model(text):
            core_seen.append(text.strip()[:60])
            if target_size:
                sizes |= _candidate_sizes(text, core, size_tokens)
    # 主干串短于 min_core_len 时 match_product 会**整条跳过**主干匹配，此时真正
    # 的失败原因是"配置把这条路关了"，不是"尺寸不符"。必须分开报，否则会误导
    # 排查方向（Best Buy 的 U8N / U7N / U6N 等 3 位主干就属于这一类）。
    min_core = int(verify.get("min_core_len", 4) or 0)
    if core_seen and core and min_core and len(core) < min_core:
        return (f"同系列({core})有候选，但主干串仅 {len(core)} 位 < "
                f"min_core_len={min_core}，主干匹配被跳过；只能靠精确匹配"
                f"（标题/SKU 里必须出现完整型号 {model}）。"
                f"若该类型号普遍存在，调低 min_core_len 或改用 core_size_tokens 尺寸守卫")
    if core_seen:
        if target_size and sizes and str(target_size) not in sizes:
            return (f"同系列({core})有货但尺寸不符：站上只有 "
                    f"{'/'.join(sorted(sizes))}\"，目标 {target_size}\"")
        if target_size and sizes:
            # 尺寸其实是对的，说明没匹配上的原因在别处（reject 词表 / 主干长度 /
            # 词边界）。若这里还报"尺寸不符"，会把排查方向带偏。
            return (f"同系列({core})有候选且尺寸含目标 {target_size}\"，"
                    f"但仍未命中——请检查 reject 词表是否误伤、主干串长度是否够、"
                    f"以及标题里型号是否被词边界挡住")
        return f"同系列({core})有候选，但被尺寸/规则判为不符"
    if len(core_seen) == 0 and any(rejected(c) for c in items):
        rejected_n = sum(1 for c in items if rejected(c))
        if rejected_n == len(items):
            return f"候选 {len(items)} 条全被 reject 规则排除（可能误伤，需核对 spec）"
    return f"该站没有这个系列（候选 {len(items)} 条，核心串 {core or want} 未出现）"


def match_product(spec: dict, candidates: Iterable[Any], brand: str = "",
                  model: str = "") -> Any:
    """三段兜底型号匹配：精确 → 主干 → None。

    candidates 为任意实现了 .sku/.title/.url（可缺省）的候选对象（SearchHit 或
    duck-typed）。语义与场景层 find_product 等价，但读 spec 的 match 段：
        - reject.title_contains_any / url_contains_any 先排除配件/无关品类
        - 精确命中（型号归一化后出现在 sku/title）优先
        - allow_core_match 时按主干再试
        - 都不中返回 None（on_no_match=skip 语义）
    """
    m = spec.get("match") or {}
    verify = m.get("verify") or {}
    reject = m.get("reject") or {}

    want = normalize_model(model)
    if not want:
        return None

    t_rejects = [str(x).lower() for x in (reject.get("title_contains_any") or [])]
    u_rejects = [str(x).lower() for x in (reject.get("url_contains_any") or [])]

    def rejected(c: Any) -> bool:
        title = str(getattr(c, "title", "") or "").lower()
        url = str(getattr(c, "url", "") or getattr(c, "sku", "") or "").lower()
        return any(t in title for t in t_rejects) or any(u in url for u in u_rejects)

    def exact_contains(c: Any, piece: str) -> bool:
        """精确命中：型号（原始串）作为独立词出现。

        用原始 sku/title（保留空格/大小写）做**词边界匹配**——允许型号前面是 2~3
        位尺寸数字前缀（系列名→带尺寸具体型号，如 A65NV→50A65NV），但结尾必须是
        词边界。防止「a 4K」这种经归一化后吞空格变成 A4K 的伪命中。
        """
        raw = (f"{getattr(c, 'sku', '') or ''} {getattr(c, 'title', '') or ''} "
               f"{getattr(c, 'url', '') or ''}").lower()
        if not raw or not piece:
            return False
        return re.search(
            r"(?:^|[^a-z0-9]|\d{2,3})" + re.escape(piece.lower())
            + r"(?=[^a-z0-9]|$)", raw) is not None

    def core_contains(c: Any, piece: str, target_size: str = "") -> bool:
        """主干匹配：同款辨识串出现，但**尺寸不一致必须拒绝**（防张冠李戴）。

        目标型号带尺寸（如 75U6SV → core U6SV）时，候选标题里若有「其他尺寸+同型号」
        （55U6SV），会把 55 寸价记到 75 寸头上——零售线红线。所以：
          候选文本含 core，且候选含带尺寸的同款（\\d{2,3}+core），
          若候选尺寸 ≠ 目标尺寸 → 拒绝。
        目标无尺寸（系列名 A65NV）时不做尺寸约束（系列名可映射任意尺寸具体型号）。

        尺寸写法不止「紧贴」一种。Costco 把标题写成
        `Hisense 65" Class - U7SG Series`，尺寸和系列被 `" Class - ` 隔开，
        紧贴式 `(\\d{2,3})core` 永远不命中 → 尺寸守卫失效 → 65 吋会被判成
        75 吋的同款。所以 spec 可用 `verify.core_size_tokens` 声明「尺寸与系列之间
        允许的分隔词」，这时改用 `_candidate_sizes` 抽全部尺寸再比对。
        """
        text = f"{getattr(c, 'sku', '') or ''} {getattr(c, 'title', '') or ''} {getattr(c, 'url', '') or ''}"
        ntext = normalize_model(text)
        if piece not in ntext:
            return False
        if target_size:
            size_tokens = verify.get("core_size_tokens") or []
            if size_tokens:
                cand_sizes = _candidate_sizes(text, piece, size_tokens)
            else:
                cand_sizes = set(re.findall(r"(\d{2,3})" + re.escape(piece), ntext))
            if cand_sizes and str(target_size) not in cand_sizes:
                return False   # 候选出现的是不同尺寸的同款（55U6SV vs 75U6SV）
        return True

    def search() -> Any:
        # 精确（词边界）
        for c in candidates:
            if rejected(c):
                continue
            if exact_contains(c, want):
                return c
        # 主干（尺寸约束：目标带尺寸时，候选同款尺寸必须一致，防 55" 记到 75" 头）
        if verify.get("allow_core_match", True):
            core = core_from_spec(verify, want)
            if len(core) >= int(verify.get("min_core_len", 4)):
                target_size = _leading_size(want)
                for c in candidates:
                    if rejected(c):
                        continue
                    if core_contains(c, core, target_size):
                        return c
        return None

    return search()


def core_from_spec(verify: dict, norm: str) -> str:
    """按 verify 配置从归一化型号取主干。"""
    return core_model(
        norm,
        strip_leading_size=bool(verify.get("core_strip_leading_size", True)),
        strip_suffix=verify.get("core_strip_suffix") or [])


# ---------------------------------------------------------------- 详情页型号校验

def payload_matches_model(model: str, payload: Any,
                          min_model_len: int = 4) -> bool:
    """详情页 payload 是否确实属于目标型号（防张冠李戴的**最后一道闸**）。

    语义与 `scripts/crawl_ca_retail.py::_payload_matches_model` 一致，抽到库里
    是为了让"直连 PDP"这类新入口也能复用同一套判据，而不是各写一份慢慢漂移。

    校验顺序（从严到宽）：
    1) **权威型号精确相等**：`product.model_candidates` 非空时**只认精确相等**，
       不等直接否掉，不再退回宽松的标题/URL 子串匹配——否则权威型号形同虚设
       （`55U7SG` 不能命中 `55U78SG`）。
    2) `product.model` > `product.title` > `product.url` 的子串匹配（URL 兜底：
       有些站标题不含型号，但 URL slug 里含）。

    min_model_len：目标型号归一化后短于此长度直接判否。默认 4——注意
    **归一化长度 2~3 的型号（如 Samsung 的 `HW` 系列）会因此永远判不中**，
    这是既有行为；确有此类型号时按站点显式调低，不要全局放宽。
    """
    want = normalize_model(model)
    if len(want) < max(1, int(min_model_len)):
        return False
    product = getattr(payload, "product", None)
    if product is None:
        return False
    candidates = list(getattr(product, "model_candidates", None) or [])
    if candidates:
        return any(normalize_model(c) == want for c in candidates if c)
    for value in (getattr(product, "model", ""), getattr(product, "title", ""),
                  getattr(product, "url", "")):
        if want in normalize_model(value):
            return True
    return False


# ---------------------------------------------------------------- P0-2: shops

def shop_columns(spec: dict) -> list[dict]:
    """返回 shops.columns 列表（multi_shop）；single_shop/无 shops 返回 []。"""
    shops = spec.get("shops") or {}
    if shops.get("mode") != "multi_shop":
        return []
    return shops.get("columns") or []


def extract_shop_prices(spec: dict, dom) -> dict[str, float]:
    """按 shops 段从商品页 DOM 抽 {原始店铺名: 最低价}（multi_shop）。

    仅当 shops.mode == multi_shop 时生效（自营站该节不适用）。
    配置形态（RetailSpec §6.2）：
        "shops": {
          "mode": "multi_shop",
          "list": {"container": ".p-priceTable_row",
                   "shop_name": ".p-priceTable_shop",
                   "price": ".p-priceTable_price", "limit": 30},
          ...
        }
    同一店铺出现多次取最低价。无 DOM / 容器不适用时返回 {}。
    """
    shops = spec.get("shops") or {}
    if shops.get("mode") != "multi_shop":
        return {}
    cfg = shops.get("list") or {}
    container = cfg.get("container") or ""
    if dom is None or not container:
        return {}
    shop_sel = cfg.get("shop_name") or ""
    price_sel = cfg.get("price") or ""
    limit = int(cfg.get("limit", 30))
    out: dict[str, float] = {}
    for block in dom.sub(container, limit=limit):
        shop = ""
        if shop_sel:
            shop = " ".join(str(block.text(shop_sel) or "").split())
            if not shop:                      # 允许店铺名在属性里（部分模板）
                for attr in ("title", "aria-label"):
                    v = block.attr(shop_sel, attr)
                    if v:
                        shop = " ".join(str(v).split())
                        break
        if not shop:
            continue
        raw = block.text(price_sel) if price_sel else ""
        price = _to_price(raw)
        if price is None:
            continue
        prev = out.get(shop)
        out[shop] = price if prev is None else min(prev, price)
    return out


def map_shop_columns(spec: dict, shop_prices: dict[str, Any]) -> dict[str, Any]:
    """把 {店铺名: 价} 映射到 shops.columns 声明的列 {列key: 价}。

    与现有 KakakuJpAdapter.map_shop_columns 等价的配置化实现：
    逐列按 match 关键词对店铺名做规范化子串匹配，同一列多店铺取最低价。
    未命中的列不出现在结果里（导出层填空）。
    """
    columns = shop_columns(spec)
    result: dict[str, Any] = {}
    for col in columns:
        key = col.get("key")
        if not key:
            continue
        keywords = [normalize_model(str(k)) for k in (col.get("match") or [])]
        best = None
        for shop_name, price in shop_prices.items():
            ns = normalize_model(str(shop_name))
            hit = any(k and k in ns for k in keywords)
            if not keywords:
                hit = ns and ns == normalize_model(key)   # 无关键词时按列 key 精确
            if not hit:
                continue
            best = _min_price(best, price)
        if best is not None:
            result[key] = best
    return result


def lowest_price(spec: dict, shop_prices: dict[str, Any]) -> tuple[Any, str]:
    """从 {店铺名: 价} 取 (最低价, 最低店铺名)。空返回 ('', '')。"""
    if not shop_prices:
        return "", ""
    best = None
    best_shop = ""
    for shop, price in shop_prices.items():
        p = _to_price(price)
        if p is None:
            continue
        if best is None or p < best:
            best = p
            best_shop = shop
    return ("" if best is None else best), best_shop


# ---------------------------------------------------------------- 通用辅助

def _to_price(v: Any) -> float | None:
    """文本/数字 → float（去货币符号、逗号、空格、非断空格）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[\d,]+(?:\.\d{1,2})?", str(v).replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _min_price(a: Any, b: Any) -> Any:
    try:
        fa = float(a)
    except (TypeError, ValueError):
        return b
    try:
        fb = float(b)
    except (TypeError, ValueError):
        return a
    return a if fa <= fb else b


# ---------------------------------------------------------------- R1-4: 评价翻页

def review_page_url(spec: dict, sku: str, page: int, *,
                    adapter: Any = None, product_url: str = "") -> str:
    """按 paginate.reviews.type 生成第 page 页评价页 URL；不翻页返回空串。

    四种 type（与 SUPPORTED_REVIEW_PAGINATE 同步）：
        url_page     ── 页号嵌在路径里，用 url_template，占位 {sku} {page}
                        例：https://review.x.com/review/{sku}/Page={page}/
        query_param  ── 页号在查询串，param 指定参数名，基于 base（评价页或商品页）
                        例：<product_url>?page=2
        click_more   ── 需点击"加载更多"，无 URL 可拼 → 返回空串，由调用方走点击路径
        none         ── 不翻页 → 返回空串

    优先用 spec 配置；spec 未配（type=none/缺失）且适配器有 reviews_url 时，
    回落到 adapter.reviews_url(sku, page)，保持对存量站点的兼容。
    """
    page = max(1, int(page or 1))
    cfg = ((spec or {}).get("paginate") or {}).get("reviews") or {}
    ptype = str(cfg.get("type") or "none")
    # 注意：start 合法值含 0（offset 型分页从 0 起），不能用 `or 1`——那会把 0
    # 当假值替换成 1，导致偏移整体偏 1。step 同理（0 无意义故仍回落 1）。
    start_raw = cfg.get("start")
    start = int(start_raw) if start_raw is not None else 1
    step = int(cfg.get("step") or 1)
    # 第 page 页对应的页号值（start 起，按 step 递增）
    page_value = start + (page - 1) * step

    if ptype == "url_page":
        tmpl = str(cfg.get("url_template") or "")
        if tmpl:
            return tmpl.replace("{sku}", str(sku or "")).replace(
                "{page}", str(page_value))
    elif ptype == "query_param":
        param = str(cfg.get("param") or "")
        base = str(cfg.get("url_template") or "") or product_url
        if param and base:
            # {base} 占位符 = spec.base_url（amazon 系 spec 的 url_template 用它）
            base = base.replace("{base}", (spec or {}).get("base_url") or "")
            base = base.replace("{sku}", str(sku or ""))
            parts = urlsplit(base)
            qs = [kv for kv in parts.query.split("&")
                  if kv and not kv.lower().startswith(param.lower() + "=")]
            qs.append(f"{param}={page_value}")
            return urlunsplit((parts.scheme, parts.netloc, parts.path,
                               "&".join(qs), ""))
    elif ptype == "click_more":
        return ""      # 无法拼 URL，调用方走点击展开路径

    # 回落：spec 未配置翻页时沿用适配器既有能力（存量站点兼容）
    if adapter is not None:
        fn = getattr(adapter, "reviews_url", None)
        if callable(fn):
            try:
                return str(fn(sku, page) or "")
            except Exception:
                return ""
    return ""


def collect_reviews_paged(spec: dict, adapter, fetcher, sku: str,
                          base_reviews: Iterable[Any], *,
                          open_dom=None, product_url: str = "",
                          max_reviews: int | None = None,
                          max_pages: int | None = None,
                          verbose: bool = True) -> tuple[list, dict]:
    """spec 驱动的评价翻页收集：按 review_key 去重累积，多重终止条件封顶。

    终止条件（任一命中即停，均不重试）——对应任务表 §4.3 的统一约定：
        1. 累计条数 >= max_reviews          → reason=max_reviews
        2. 本页无新评价（key 全已知）        → reason=page_all_known
        3. 页数 >= max_pages（安全阀）       → reason=max_pages
        4. 拼不出下一页 URL                  → reason=no_more_url
        5. res.blocked / captcha             → reason=blocked（带 block_reason）
        6. HTTP 非 ok 连续 2 次              → reason=http_failed（带 status）

    返回 (reviews, meta)。meta 含 stop_reason / pages_fetched / blocked /
    block_reason / http_status，供上层汇报与站级熔断判断，绝不静默。
    """
    if open_dom is None:
        from .scenarios import open_dom as _od
        open_dom = _od

    cap = resolve_max_reviews(spec, max_reviews)
    pages_cap = resolve_max_review_pages(spec, max_pages)
    cfg = ((spec or {}).get("paginate") or {}).get("reviews") or {}
    inc = (spec or {}).get("incremental") or {}
    stop_when = str(inc.get("stop_when") or "page_all_known")
    no_new_rounds_cap = max(1, int(inc.get("no_new_rounds") or 1))
    # 翻页类型：四种 URL 型 + filter_sweep（Amazon 专用组合扫描）
    ptype = str(cfg.get("type") or "none")

    out = list(base_reviews or [])
    seen = {getattr(r, "review_key", None) for r in out}
    meta: dict = {"pages_fetched": 0, "blocked": False, "block_reason": "",
                  "http_status": 0, "stop_reason": "",
                  "paginate_type": str(cfg.get("type") or "none"),
                  "max_reviews": cap, "max_pages": pages_cap}

    if cap <= 0:
        meta["stop_reason"] = "max_reviews"
        return out[:0], meta
    if len(out) >= cap:
        meta["stop_reason"] = "max_reviews"
        return out[:cap], meta

    rv_cfg = {}
    try:
        rv_cfg = (getattr(adapter, "schema", {}) or {}).get("reviews") or {}
    except Exception:
        rv_cfg = {}
    wait_selector = str((spec.get("reviews") or {}).get("container")
                        or rv_cfg.get("container") or "")

    # ---- click_more 分支：点「加载更多」展开评价（SPA 评价流）----
    # review_page_url 对 click_more 返回空串，URL 循环拿不到下页，故在此单独走
    # 点击路径：直接用 fetcher.page()（绕过 open_dom 的单次点击限制），在一次打开里
    # 用 click_selectors 定位「ver más」按钮、click_repeats 重复点击、click_growth_selector
    # 监测评价节点增长、无新增时自动停。每次打开后抽取全部评价去重累积。
    # 配置来自 spec.paginate.reviews：click_selector（CSS）+ click_repeats（次数）
    # + reviews.container（增长监测）。非浏览器 fetcher 无 .page()，直接退回首屏评价。
    if str(cfg.get("type") or "none") == "click_more" and product_url:
        page_fn = getattr(fetcher, "page", None)
        if not callable(page_fn):
            # HTTP fetcher 不支持点击 → 首屏评价即全部，如实记录后返回
            meta["stop_reason"] = "no_browser_click_more"
            return out[:cap], meta
        click_selector = str(cfg.get("click_selector") or "")
        clicks = max(1, int(cfg.get("click_repeats") or pages_cap))
        # 增长监测选择器：评价容器，用于判断点击是否真的展开新评价
        growth_sel = str((spec.get("reviews") or {}).get("container")
                        or rv_cfg.get("container") or "")
        no_new_streak_cm = 0
        for open_round in range(1, pages_cap + 1):
            if len(out) >= cap:
                meta["stop_reason"] = "max_reviews"
                break
            try:
                # click_selectors 走 CSS 重复点击 + 增长监测；无 click_selector 时
                # 退而用 click_texts（文本匹配，每文本点 1 次，无增长监测）。
                if click_selector:
                    with page_fn(product_url, wait_selector=wait_selector,
                                 click_selectors=[click_selector],
                                 click_repeats=clicks,
                                 click_growth_selector=growth_sel,
                                 scroll_passes=14,
                                 extra_wait_ms=3500) as (res, dom):
                        html = dom.html() if dom is not None else ""
                else:
                    click_texts = cfg.get("click_texts") or [
                        "ver más", "ver mas", "mostrar más", "mostrar mas",
                        "load more", "show more", "view more", "see more",
                    ]
                    with open_dom(adapter, fetcher, product_url,
                                  wait_selector=wait_selector,
                                  click_texts=click_texts,
                                  scroll_passes=14,
                                  extra_wait_ms=3500) as (res, dom, html):
                        pass
                meta["pages_fetched"] += 1
                meta["http_status"] = int(getattr(res, "status", 0) or 0)
                if getattr(res, "blocked", False):
                    meta["blocked"] = True
                    meta["block_reason"] = str(getattr(res, "block_reason", "")
                                               or "blocked")
                    meta["stop_reason"] = "blocked"
                    if verbose:
                        print(f"       评价点击轮 {open_round} 被拦截："
                              f"{meta['block_reason']}")
                    break
                if not getattr(res, "ok", False) or dom is None:
                    meta["stop_reason"] = meta["stop_reason"] or "http_failed"
                    break
                page_reviews = adapter.extractor(html).reviews(dom)
            except Exception as e:
                meta["stop_reason"] = "exception"
                meta["block_reason"] = f"{type(e).__name__}: {e}"
                break
            fresh = [r for r in page_reviews
                     if getattr(r, "review_key", None) not in seen]
            for r in fresh:
                seen.add(getattr(r, "review_key", None))
                out.append(r)
                if len(out) >= cap:
                    break
            if len(out) >= cap:
                meta["stop_reason"] = "max_reviews"
                break
            if not fresh:
                no_new_streak_cm += 1
                if (stop_when == "page_all_known"
                        or no_new_streak_cm >= no_new_rounds_cap):
                    meta["stop_reason"] = "page_all_known"
                    break
            else:
                no_new_streak_cm = 0
        else:
            meta["stop_reason"] = meta["stop_reason"] or "max_pages"
        return out[:cap], meta

    # ---- 分支：filter_sweep（Amazon 专用，实测唯一能突破首屏的方式）----
    # 背景：Amazon 的 pageNumber 参数对独立评价页**无效**（p1/p2 返回同一批
    # 10 条），且 /hz/reviews-render/ajax/reviews/get/ 恒 403（即使已登录）。
    # 实测有效的是 **sortBy / filterByStar 组合**：每个组合返回一屏不同的
    # 前 N 条，多组合取并集即可累积。实测 43E6SF：PDP 首屏 8 → 35 条（4.4x）。
    if ptype == "filter_sweep":
        combos = cfg.get("combos") or []
        tmpl = str(cfg.get("url_template") or "")
        if not tmpl or not combos:
            meta["stop_reason"] = "filter_sweep_misconfigured"
            return out[:cap], meta
        # ---- 站点首屏硬顶快速退出（省掉整轮扫描）----
        # 实测 amazon.com 对登录会话也只渲染 8 条（总评分 2,742 的产品同样只给 8 条），
        # 此时跑完整 11 组合是 100% 无效请求（实测 ~35s/型号，占单型号总耗时一半）。
        # 配置首屏硬顶后：已达标即直接返回，不再发请求。
        # 注意只对"有硬顶的站点"配置；CA/MX 首屏可能不是上限，不配即保持原行为。
        first_screen_cap = int(cfg.get("first_screen_cap") or 0)
        if first_screen_cap > 0 and len(out) >= first_screen_cap:
            meta["stop_reason"] = "first_screen_cap"
            return out[:cap], meta
        seen_keys = {getattr(r, "review_key", None) for r in out}
        idle_streak = 0
        soft_block_streak = 0
        for combo in combos:
            if len(out) >= cap:
                meta["stop_reason"] = "max_reviews"
                break
            rurl = (tmpl.replace("{base}", (spec or {}).get("base_url") or "")
                    .replace("{sku}", str(sku or "")))
            for k, v in (combo or {}).items():
                rurl = rurl.replace("{%s}" % k, str(v))
            try:
                with open_dom(adapter, fetcher, rurl,
                              wait_selector=wait_selector) as (res, dom, html):
                    meta["pages_fetched"] += 1
                    meta["http_status"] = int(getattr(res, "status", 0) or 0)
                    if getattr(res, "blocked", False):
                        reason = str(getattr(res, "block_reason", "") or "blocked")
                        # ① 登录墙 ≠ 反爬：会话失效不该把整轮扫描判成 blocked。
                        if reason == "not_logged_in":
                            meta["block_reason"] = reason
                            meta["stop_reason"] = meta["stop_reason"] or \
                                "not_logged_in"
                            if verbose:
                                print(f"       组合 {combo} 命中登录墙，跳过")
                            continue
                        # ② "缺锚点/正文过短"绝大多数是**该 filter 本来就没有
                        # 评价**的空结果——实测 filterByStar=two_star 在无 2 星
                        # 评价时即返回无锚点页。空结果是正常业务结果，绝不能
                        # 中断整个扫描（曾因此把 35 条截断成 14 条）。
                        # 真被拦时会**连续**命中，故用"连续 2 次"兜底：既不被
                        # 单个空 filter 打断，也不静默放过持续性拦截。
                        if reason.startswith(("no_anchor_element", "too_short")):
                            soft_block_streak += 1
                            meta["block_reason"] = reason
                            if verbose:
                                print(f"       组合 {combo} 空结果（{reason}），跳过"
                                      f"（连续 {soft_block_streak}）")
                            if soft_block_streak >= 2:
                                meta["blocked"] = True
                                meta["stop_reason"] = "blocked"
                                if verbose:
                                    print("       连续空/缺锚点 → 判定真反爬，停止扫描")
                                break
                            continue
                        # ③ 其余（http_403/429/503、验证码 marker 等）一律真反爬
                        meta["blocked"] = True
                        meta["block_reason"] = reason
                        meta["stop_reason"] = "blocked"
                        if verbose:
                            print(f"       评价组合 {combo} 被拦截：{reason}")
                        break
                    # 有正常响应 → 软拦截计数复位。
                    # 关键：空 filter 之间通常夹杂正常响应，若不复位，
                    # 多个空 filter 会被误累积成"连续 2 次"从而误判真反爬。
                    soft_block_streak = 0
                    if not getattr(res, "ok", False) or dom is None:
                        continue
                    page_reviews = adapter.extractor(html).reviews(dom)
            except Exception as e:
                meta["stop_reason"] = meta["stop_reason"] or "exception"
                meta["block_reason"] = f"{type(e).__name__}: {e}"
                continue
            fresh = [r for r in page_reviews
                     if getattr(r, "review_key", None) not in seen_keys]
            if verbose:
                print(f"       组合 {combo}: 本屏 {len(page_reviews)} 条，"
                      f"新增 {len(fresh)} 条")
            if not fresh:
                idle_streak += 1
                if idle_streak >= max(1, int(cfg.get("no_new_combos_cap") or 4)):
                    meta["stop_reason"] = "page_all_known"
                    break
            else:
                idle_streak = 0
            for r in fresh:
                seen_keys.add(getattr(r, "review_key", None))
                out.append(r)
                if len(out) >= cap:
                    break
        else:
            meta["stop_reason"] = meta["stop_reason"] or "combos_exhausted"
        return out[:cap], meta

    http_fail_streak = 0
    no_new_streak = 0
    for page in range(1, pages_cap + 1):
        rurl = review_page_url(spec, sku, page, adapter=adapter,
                               product_url=product_url)
        if not rurl:
            meta["stop_reason"] = meta["stop_reason"] or "no_more_url"
            break
        try:
            with open_dom(adapter, fetcher, rurl,
                          wait_selector=wait_selector) as (res, dom, html):
                meta["pages_fetched"] += 1
                meta["http_status"] = int(getattr(res, "status", 0) or 0)
                # 反爬：立即停并如实汇报，不重试（对应 §4.3 第 4 条）
                if getattr(res, "blocked", False):
                    meta["blocked"] = True
                    meta["block_reason"] = str(getattr(res, "block_reason", "")
                                               or "blocked")
                    meta["stop_reason"] = "blocked"
                    if verbose:
                        print(f"       评价 p{page} 被拦截：{meta['block_reason']}")
                    break
                if not getattr(res, "ok", False) or dom is None:
                    http_fail_streak += 1
                    if verbose:
                        print(f"       评价 p{page} HTTP {meta['http_status']} 失败")
                    if http_fail_streak >= 2:
                        meta["stop_reason"] = "http_failed"
                        break
                    continue
                http_fail_streak = 0
                page_reviews = adapter.extractor(html).reviews(dom)
        except Exception as e:
            meta["stop_reason"] = "exception"
            meta["block_reason"] = f"{type(e).__name__}: {e}"
            break

        if not page_reviews:
            meta["stop_reason"] = "empty_page"
            break

        fresh = [r for r in page_reviews
                 if getattr(r, "review_key", None) not in seen]
        for r in fresh:
            seen.add(getattr(r, "review_key", None))
            out.append(r)
            if len(out) >= cap:
                break
        if len(out) >= cap:
            meta["stop_reason"] = "max_reviews"
            break
        if not fresh:
            no_new_streak += 1
            if stop_when == "page_all_known" or no_new_streak >= no_new_rounds_cap:
                meta["stop_reason"] = "page_all_known"
                break
        else:
            no_new_streak = 0
    else:
        meta["stop_reason"] = meta["stop_reason"] or "max_pages"

    return out[:cap], meta
