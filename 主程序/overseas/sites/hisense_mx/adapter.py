"""Hisense 墨西哥官网 TV SPEC 适配器（多级流程）。

结构与 hisense_ca 高度相似（同一套企业站模板），入口为墨西哥站电视频道：
- TV 总览 https://hisense.com.mx/televisores 列出各电视产品页链接；
- 产品页 = 单页单型号，当前成功页面的主要规格结构是
  section#specifications .columns-wrapper .column-item（h5=项目、p=值），
  .grid-especificaciones 与 <table> 作为兼容回退；西语标签照样成对解析。

按「系列」分组：型号去掉前导尺寸数字得到系列码，同系列多型号横排合并成一张表。
选择器为「以实测为准」的初值：DOM 改版或与实测不符时只需调整本文件的正则/选择器，
不影响核心框架与其它线。只做 SPEC 采集，不支持搜索/评价（那是零售站职责）。
"""
from __future__ import annotations

import re

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_table
from ...dict import translate
from ...fetchers import Dom
from .. import SiteAdapter

_BASE = "https://hisense.com.mx"
ENTRY = f"{_BASE}/televisores"

# 实测（2026-09）入口页结构：
#   电视总览 /televisores 每个产品卡片有：
#     <a href="/<slug>" class="main-btn-white ...">Conoce más</a>   ← 产品页链接
#     <button ... data-product-sku="75U8N">Donde Comprar</button>   ← 型号/SKU（权威）
#   slug 形如 /uled-miniled-u8n /smart-tv-4k-a65nv /qled-qd7q /85QD6N /miniled-u8k-100。
#   产品页的核心规格区 section#specifications .columns-wrapper 目前在成功页面直接可见；
#   .grid-especificaciones 和 table 作为兼容路径。没有规格区块的产品页仍可能返回 HTTP 200，
#   因此型号页等待只使用 #productGeneral，空页由 classify_spec_empty 明确诊断。
# 非电视产品的 slug：使用完整 token 边界，避免把未来合法型号中的 c2/m2 子串误排。
_NON_TV_SLUG_RE = re.compile(
    r"(?:^|[-_/])(?:pl2|px3(?:-pro)?|c2(?:-ultra)?|l9q|l9g|"
    r"projector|proyector|laser|lser|m2-pro)(?:[-_/]|$)",
    re.I,
)
# data-product-sku 里的脏值：Amazon ASIN（B0 开头 10 位）、纯 slug（含连字符且无数字）跳过。
_ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")


def _clean_sku(sku: str) -> str:
    """规范化 data-product-sku 为型号：去空格、大写。"""
    return (sku or "").strip().upper().replace(" ", "")


def _looks_like_model(sku: str) -> bool:
    """判断 data-product-sku 是否像真实电视型号（排除 ASIN / 纯 slug）。"""
    s = _clean_sku(sku)
    if not s or _ASIN_RE.match(s):
        return False
    if s in {"UX"}:
        return True
    if "-" in (sku or "") and not re.search(r"\d", s):
        return False                      # 纯 slug 形态（如 S7N-CANVAS-TV）
    return bool(re.search(r"[A-Z]", s) and re.search(r"\d", s))


def _model_from_slug(slug: str) -> str:
    """当入口 SKU 是 ASIN/脏值时，从产品 slug 取得电视型号。"""
    low = (slug or "").strip().lower().rstrip("/")
    if re.search(r"(?:^|/)s7n(?:-|$)", low):
        return "S7N"
    m = re.search(r"(?:^|/)ux[-_]?([0-9]{2,3})(?:-|$)", low)
    if m:
        return f"UX{m.group(1)}"
    if low.endswith("/ux") or low.endswith("-ux"):
        return "UX"
    tail = low.split("/")[-1]
    tail = tail.split("-")[-1]
    return re.sub(r"[^A-Za-z0-9]", "", tail).upper()


def _model_for_pair(slug: str, sku: str) -> str:
    cleaned = _clean_sku(sku)
    slug_low = (slug or "").lower()
    slug_model = _model_from_slug(slug)
    if _ASIN_RE.match(cleaned):
        return slug_model or cleaned
    if "canvas" in slug_low and cleaned.startswith("S7N"):
        return "S7N"
    if cleaned.startswith("UX-") and "RGB" in cleaned:
        return slug_model or cleaned.replace("-", "")
    return cleaned.replace("-", "") if cleaned else slug_model


def _series_of(model: str) -> str:
    """从型号推导系列码：去掉前导尺寸数字。75U8N → U8N；U6N → U6N。"""
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()

class HisenseMxAdapter(SiteAdapter):
    code = "hisense_mx"
    name = "Hisense（墨西哥）"
    base_url = _BASE
    country = "墨西哥"
    channel = "Hisense 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a.main-btn-white[href]"
    spec_entry_extra_wait_ms = 3000
    # 总览有一个 verMasBtn（Ver más）入口；点击后以产品链接数量增长为终止条件。
    spec_entry_scroll_passes = 4
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a.main-btn-white[href]"
    spec_entry_scroll_max_passes = 20
    spec_entry_scroll_stable_rounds = 2
    spec_entry_click_selectors = ["a.verMasBtn"]
    spec_entry_click_repeats = 20
    spec_entry_click_wait_ms = 3000
    spec_entry_click_growth_selector = "a.main-btn-white[href]"
    spec_entry_expected_model_count = 21
    spec_entry_expected_series_count = 21
    # 等所有产品页都稳定存在的锚点 #productGeneral（避免等 .columns-wrapper 在
    # 无规格页超时被误判 failed 触发熔断）。核心规格在 section#specifications
    # .columns-wrapper（h5=项目/p=值）；无规格页解析返回空 → spec_crawl 记 empty。
    spec_page_wait = "#productGeneral"
    # 型号页偶发首屏资源超过全局 15 秒；延长导航等待，不改变反爬检测。
    spec_page_nav_timeout_ms = 60000

    def spec_entry_url(self) -> str:
        return ENTRY

    # 入口页卡片：<a href="/<slug>" class="main-btn-white ...">Conoce más</a>
    # 紧跟 <button ... data-product-sku="<型号>" ...>。用正则按出现顺序配对，
    # 取每个产品卡片的 (slug, sku)。slug 用于产品页 URL，sku 是权威型号。
    _CARD_RE = re.compile(
        r'href="(/[^"#?]+)"[^>]*class="[^"]*main-btn-white[^"]*"'
        r'.*?data-product-sku="([^"]+)"',
        re.I | re.S)
    # slug→型号 的映射缓存（model_from_url 用），series_entries 时填充。
    _slug_model: dict[str, str]

    def __init__(self):
        super().__init__()
        self._slug_model = {}

    def series_entries(self, open_dom):
        """电视总览 → 按系列分组的 (系列, [产品页URL...])。

        用 (slug, data-product-sku) 配对，slug 建 URL、sku 作型号；过滤投影仪与脏 sku。
        同时保留入口审计，区分原始卡片、非电视、无效 SKU、重复型号和最终电视数。
        """
        entry_meta: dict[str, object] = {}
        with open_dom(ENTRY, self.spec_entry_wait) as (res, dom, html):
            entry_meta = dict(getattr(res, "meta", {}) or {})
            if dom is None or not res.ok:
                self.last_entry_audit = {
                    "requested_url": ENTRY,
                    "termination_reason": "entry_failed",
                    "details": {"status": res.status, "error": res.error,
                                "block_reason": res.block_reason},
                }
                return []
            pairs = self._CARD_RE.findall(html or "")

        model_url: dict[str, str] = {}
        filtered_non_tv = 0
        invalid_model = 0
        duplicate_model = 0
        for slug, sku in pairs:
            low = (slug or "").lower()
            if _NON_TV_SLUG_RE.search(low):
                filtered_non_tv += 1
                continue
            model = _model_for_pair(slug, sku)
            if not _looks_like_model(model):
                invalid_model += 1
                continue
            url = slug if slug.startswith("http") else _BASE + slug
            if model in model_url:
                duplicate_model += 1
                continue
            model_url[model] = url
            self._slug_model[url] = model

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        termination = str(
            entry_meta.get("click_termination")
            or entry_meta.get("scroll_termination")
            or "stable"
        )
        self.last_entry_audit = {
            "requested_url": ENTRY,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination,
            "details": {
                "raw_card_pairs": len(pairs),
                "filtered_non_tv": filtered_non_tv,
                "invalid_model": invalid_model,
                "duplicate_model": duplicate_model,
                "entry_meta": entry_meta,
                "ver_mas_selector": "a.verMasBtn",
            },
        }
        return [(series, urls) for series, urls in series_map.items()]

    def model_from_url(self, url: str) -> str:
        if url in self._slug_model:
            return self._slug_model[url]
        # 兜底：从 slug 末段取型号形态
        m = re.search(r"/([a-z0-9]+(?:-[a-z0-9]+)*)$", (url or "").lower())
        slug_model = _model_from_slug(url)
        if _looks_like_model(slug_model):
            return slug_model
        return url

    def classify_spec_empty(self, dom: Dom | None, series: str = "",
                             model: str = "", url: str = "") -> str:
        """区分官网没有规格区块与规格区块存在但未解析出行。

        这是型号级审计信息，不改变 SpecSheet 的数据结构，也不把空页伪装成成功。
        "no_spec_section" 表示产品页没有公开的 SPEC 区块；
        "spec_section_no_rows" 表示区块存在，但当前支持的容器没有可解析项目；
        "spec_rows_unparsed" 表示存在候选容器但最终没有生成行。
        "empty" 仅作为 DOM 不可用或未知情况的兜底。
        """
        if dom is None:
            return "empty"
        if dom.count("section#specifications") == 0:
            return "no_spec_section"
        candidate_count = sum(dom.count(selector) for selector in (
            "section#specifications .columns-wrapper .column-item",
            "section#specifications .grid-especificaciones .grid__item",
            "section#specifications table",
        ))
        if candidate_count == 0:
            return "spec_section_no_rows"
        return "spec_rows_unparsed"

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        """解析产品页规格区 <section id="specifications">。

        实测结构（2026-09）：成功页主要使用
          .columns-wrapper .column-item（h5=项目、p=值）；
        .grid-especificaciones 与 <table> 作为兼容路径保留。若页面没有
        SPEC 区块或候选容器仍为空，则返回空表，由 spec_crawl 记录明确的
        空页诊断，不覆盖既有数据。
        """
        from ...models import SpecRow, SpecCell
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty

        sheet = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        order = 0
        seen: set[tuple[str, str]] = set()

        # 优先：section#specifications 里的 .columns-wrapper（实测这里是服务端渲染的
        # 核心规格）。每项 <div class="column-item"><h5>项目</h5><p>值</p></div>。
        for col in dom.sub("section#specifications .columns-wrapper .column-item",
                           limit=60):
            key = " ".join((col.text("h5") or "").split())
            value = " ".join((col.text("p") or "").split())
            if not key:
                continue
            k2 = ("Especificaciones", key)
            if k2 in seen:
                continue
            seen.add(k2)
            order += 1
            sheet.rows.append(SpecRow(
                category="Especificaciones", item_ja=key, item_zh=translate(key),
                values=[SpecCell(model, value)] if value else [], order=order))

        # 补充：section#specifications 里的 grid 结构（CMS 填了详细规格时才有值）
        grids = dom.sub("section#specifications .grid-especificaciones", limit=20)
        for grid in grids:
            category = (grid.text("p") or "").strip()
            for item in grid.sub(".grid__item", limit=200):
                txt = " ".join((item.self_text() or "").split())
                if not txt:
                    continue
                # grid__item 常见形态：「项目名 值」或「项目名: 值」；尽力拆分。
                key = value = ""
                for sep in (":", "："):
                    if sep in txt:
                        k, _, v = txt.partition(sep)
                        key, value = k.strip(), v.strip()
                        break
                if not key:
                    # 无分隔符时用子元素：常见 <span>/<p>/<strong> 名 + 值
                    parts = [p.strip() for p in item.texts("span, p, strong, div") if p.strip()]
                    parts = [p for p in dict.fromkeys(parts)]
                    if len(parts) >= 2:
                        key, value = parts[0], " ".join(parts[1:])
                    else:
                        key, value = txt, ""
                if not key:
                    continue
                k2 = (category, key)
                if k2 in seen:
                    continue
                seen.add(k2)
                order += 1
                sheet.rows.append(SpecRow(
                    category=category, item_ja=key, item_zh=translate(key),
                    values=[SpecCell(model, value)] if value else [], order=order))
        if sheet.rows:
            return sheet

        # 兼容：若将来改成 <table>
        tables = dom.sub("section#specifications table", limit=1) or dom.sub("table", limit=1)
        if tables:
            return parse_en_kv_table(tables[0], self.code, self.name, series, model, url)
        return empty
