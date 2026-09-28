"""Samsung 墨西哥官网 TV SPEC 适配器（多级流程）。

入口：https://www.samsung.com/mx/tvs/all-tvs/ 列出全部电视，卡片链接进型号页；
型号页规格当前位于 #specs.pdd32-product-spec accordion：按分类分组，
每项使用 .pdd32-product-spec__content-item-title/.desc；旧版 .spec-list /
.specs / table 键值结构保留为回退。折叠内容使用 textContent 读取，走本地标准化解析。

Samsung 站强 JS 渲染 + 懒加载，需浏览器 + 滚动。选择器为「以实测为准」的初值，
DOM 改版只需调整本文件；只做 SPEC，不支持搜索/评价。
"""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_table
from ...dict import translate
from ...extract import jsonld_blocks
from ...fetchers import Dom
from .. import SiteAdapter

_BASE = "https://www.samsung.com"
ENTRY = f"{_BASE}/mx/tvs/all-tvs/"

# 实测（2026-09）：all-tvs 页含 JSON-LD ItemList（numberOfItems=23），每个 item 有
# 完整产品页 url、型号（URL 末段如 un100m90hfxzx / qn83s95haexzx / mrn85r95hafxzx）、
# aggregateRating.reviewCount。从 ItemList 取产品最可靠（DOM 是 React 渲染的）。
# 型号页规格实测为 #specs.pdd32-product-spec accordion（16 个分类、124 个条目样本），
# 不是 table；parse_spec_model 优先读取 accordion，再回退旧版键值表。

# 从产品页 URL 末段提取 Samsung 型号码：
#   .../smart-tv-un100m90hfxzx/  → UN100M90HFXZX
#   .../ls03he-85-inch-qn85ls03hefxzx/ → QN85LS03HEFXZX
#   .../f-85qn990fq99f/ → 组合套装，型号取 F-85QN990FQ99F（含 combo，登记但可后续过滤）
_MODEL_TAIL_RE = re.compile(r"([a-z]{2,3}\d{2,3}[a-z0-9]+)/?(?:\?|$)", re.I)
_COMBO_RE = re.compile(r"^f-", re.I)
_NON_TV_URL_RE = re.compile(r"combo|soundbar|audio|barra[- ]de[- ]sonido", re.I)
_NON_TV_TEXT_RE = re.compile(
    r"combo|soundbar|audio|barra\s+de\s+sonido|speaker|bocina",
    re.I,
)
_FINDER_RESPONSE_PATTERN = (
    r"searchapi\\.samsung\\.com/v6/front/b2c/product/finder/newhybris"
)
_RESULT_COUNT_RE = re.compile(r"(?<!\d)(\d{1,4})\s+Resultados?\b", re.I)


def _reported_result_count(html: str, visible_text: str) -> int | None:
    """提取正文结果数，优先使用可见文本而不是隐藏 API/模板文案。"""
    visible = [
        int(value) for value in _RESULT_COUNT_RE.findall(visible_text or "")
    ]
    if visible:
        return max(visible)
    plain_html = unescape(re.sub(r"<[^>]*>", " ", html or ""))
    candidates = [
        int(value) for value in _RESULT_COUNT_RE.findall(plain_html)
    ]
    return max(candidates) if candidates else None


_FINDER_JS = r"""async () => {
  const base = "https://searchapi.samsung.com/v6/front/b2c/product/finder/newhybris";
  const bodies = [];
  const pageSize = 10;
  let start = 1;
  let status = 0;
  try {
    for (let page = 0; page < 20; page++) {
      const query = "?type=04010000&siteCode=mx&start=" + start
        + "&num=" + pageSize + "&sort=newest"
        + "&onlyFilterInfoYN=N&keySummaryYN=Y&filter7=07s01";
      const response = await fetch(base + query, {
        headers: {accept: "application/json"}
      });
      status = response.status;
      if (!response.ok) break;
      const data = await response.json();
      const resultData = data && data.response && data.response.resultData || {};
      const productList = resultData.productList || [];
      bodies.push(JSON.stringify(data));
      if (!Array.isArray(productList) || productList.length < pageSize) break;
      start += pageSize;
    }
    return {status, bodies};
  } catch (error) {
    return {status: status || -1, bodies, error: String(error)};
  }
}"""


def _model_from_samsung_url(url: str) -> str:
    base = (url or "").split("?")[0].rstrip("/")
    seg = base.split("/")[-1] if base else ""
    m = _MODEL_TAIL_RE.search(seg)
    if m:
        return m.group(1).upper()
    # combo 套装 f-85qn990fq99f 之类
    if _COMBO_RE.match(seg):
        return seg.upper()
    return ""


def _series_of(model: str) -> str:
    """Samsung 型号取系列：去掉前缀 UN/QN/MRN + 尺寸数字。
    UN100M90HFXZX→M90HFXZX；QN83S95HAEXZX→S95HAEXZX；尽力而为。"""
    s = re.sub(r"^(?:UN|QN|MRN|LS)?\d{2,3}", "", (model or "").upper())
    return s or (model or "").upper()


class SamsungMxAdapter(SiteAdapter):
    code = "samsung_mx"
    name = "Samsung（墨西哥）"
    base_url = _BASE
    country = "墨西哥"
    channel = "Samsung 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "script[type='application/ld+json'], a[href*='/mx/tvs/']"
    spec_entry_extra_wait_ms = 3000
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/mx/tvs/']"
    spec_entry_scroll_max_passes = 60
    spec_entry_scroll_stable_rounds = 3
    spec_entry_click_texts = ["Ver más"]
    # 入口列表的 finder API 返回各系列下的 modelList（不同尺寸）；
    # response_bodies 由 BrowserFetcher 在滚动/Ver más 后统一捕获。
    spec_entry_response_pattern = _FINDER_RESPONSE_PATTERN
    spec_entry_response_limit = 2_000_000
    # 入口正文显示 58 Resultado；API 当前展开为 80 个尺寸型号、19 个纯电视系列。
    # 58 是结果卡口径，不等同于系列数；详情写入入口审计。
    spec_entry_expected_model_count = 80
    spec_entry_expected_series_count = 19
    # 实际 MX 产品页含公开 #specs 区；仅等待产品/规格锚点，避免在
    # captcha 正常脚本例外下把真正无业务内容的挑战页放行。
    spec_page_wait = "#specs.pdd32-product-spec, #pdp-root, main"
    spec_page_nav_timeout_ms = 60000
    # 现场确认 MX 正常产品页的 HTML 含 reCAPTCHA 脚本引用，通用 marker
    # 会误报；HTTP 状态、正文长度、业务锚点等其它保护判据仍然生效。
    block_marker_allowlist = ("captcha",)

    def spec_entry_url(self) -> str:
        return ENTRY

    def _iter_itemlist_urls(self, html: str):
        """从 JSON-LD ItemList 产出产品页 URL（最可靠）。"""
        for block in jsonld_blocks(html):
            nodes = block if isinstance(block, list) else [block]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if str(node.get("@type")) != "ItemList":
                    continue
                for el in node.get("itemListElement") or []:
                    item = el.get("item") if isinstance(el, dict) else None
                    url = ""
                    if isinstance(item, dict):
                        url = item.get("url") or ""
                    elif isinstance(el, dict):
                        url = el.get("url") or ""
                    if url:
                        clean = url.split("?")[0]
                        if _NON_TV_URL_RE.search(clean):
                            continue
                        yield clean

    @staticmethod
    def _parse_finder_bodies(
        response_bodies: list[str],
        fallback_urls: dict[str, str],
    ) -> tuple[list[tuple[str, list[str]]], dict[str, object]]:
        """解析 Samsung finder 分页响应中的系列和各尺寸型号。"""
        buckets: dict[str, dict[str, object]] = {}
        order: list[str] = []
        seen_models: set[str] = set()
        product_count = 0
        response_errors: list[str] = []
        filtered_non_tv = 0
        missing_pdp = 0
        duplicate_models = 0
        finder_info_summaries: list[dict[str, object]] = []
        api_total_records: set[int] = set()

        def add_finder_summary(source: str, value: object) -> None:
            if isinstance(value, dict):
                summary: dict[str, object] = {"source": source}
                for key, item in value.items():
                    key_text = str(key)
                    lower_key = key_text.lower()
                    if (isinstance(item, (str, int, float, bool))
                            or item is None) and (
                                "count" in lower_key or "record" in lower_key
                                or key_text in {"siteCode", "fromRecord", "toRecord"}
                            ):
                        summary[key_text] = item
                if len(summary) == 1:
                    summary["keys"] = sorted(str(key) for key in value)[:40]
            elif isinstance(value, list):
                summary = {"source": source, "type": "list",
                           "length": len(value)}
            else:
                summary = {"source": source, "type": type(value).__name__,
                           "value": value}
            if summary not in finder_info_summaries:
                finder_info_summaries.append(summary)

        for body in response_bodies:
            try:
                payload = json.loads(body or "")
            except (TypeError, ValueError) as exc:
                response_errors.append(f"{type(exc).__name__}: {exc}")
                continue
            response = payload.get("response") if isinstance(payload, dict) else {}
            result_data = response.get("resultData") if isinstance(response, dict) else {}
            if isinstance(result_data, dict):
                common = result_data.get("common")
                if common is not None:
                    add_finder_summary("common", common)
                    if isinstance(common, dict):
                        for key in ("totalRecord", "totalCount", "totalResults",
                                    "resultCount"):
                            raw_count = common.get(key)
                            try:
                                if raw_count is not None:
                                    api_total_records.add(int(raw_count))
                            except (TypeError, ValueError):
                                pass
                if "finderInfo" in result_data:
                    add_finder_summary("finderInfo", result_data.get("finderInfo"))
            product_list = result_data.get("productList") if isinstance(result_data, dict) else []
            if not isinstance(product_list, list):
                continue
            product_count += len(product_list)
            for family_index, family in enumerate(product_list):
                if not isinstance(family, dict):
                    continue
                family_id = str(
                    family.get("familyId")
                    or family.get("productGroupId")
                    or family.get("fmyId")
                    or family.get("fmyMarketingName")
                    or f"idx-{family_index}"
                ).strip()
                series_name = ""
                for key in ("fmyEngName", "fmyMarketingName", "familyName", "fmyName"):
                    value = str(family.get(key) or "").strip()
                    if value:
                        series_name = value.upper()
                        break
                models = family.get("modelList") or family.get("models") or []
                if not isinstance(models, list):
                    continue
                if family_id not in buckets:
                    buckets[family_id] = {
                        "series": series_name or family_id.upper(),
                        "models": {},
                    }
                    order.append(family_id)
                bucket_models = buckets[family_id]["models"]
                if not isinstance(bucket_models, dict):
                    continue
                for item in models:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("modelCode") or item.get("code") or "").strip().upper()
                    if not code or not re.search(r"[A-Z]", code) or not re.search(r"\d", code):
                        continue
                    display = " ".join(str(
                        item.get("displayName") or item.get("name") or ""
                    ).split())
                    pdp = str(
                        item.get("pdpUrl")
                        or item.get("originPdpUrl")
                        or item.get("productUrl")
                        or ""
                    ).strip()
                    url = fallback_urls.get(code, "")
                    if pdp:
                        url = pdp if pdp.startswith("http") else urljoin(
                            _BASE, "/" + pdp.lstrip("/")
                        )
                    if _NON_TV_TEXT_RE.search(display) or _NON_TV_URL_RE.search(url):
                        filtered_non_tv += 1
                        continue
                    if not url:
                        missing_pdp += 1
                        continue
                    if code in seen_models:
                        duplicate_models += 1
                        continue
                    seen_models.add(code)
                    bucket_models[code] = url

        grouped: dict[str, list[str]] = {}
        for family_id in order:
            bucket = buckets[family_id]
            models = bucket.get("models")
            if not isinstance(models, dict) or not models:
                continue
            for url in models.values():
                model = _model_from_samsung_url(str(url))
                series = _series_of(model) if model else str(
                    bucket.get("series") or family_id
                )
                grouped.setdefault(series, [])
                if url not in grouped[series]:
                    grouped[series].append(url)
        entries = list(grouped.items())
        return entries, {
            "response_product_count": product_count,
            "response_model_count": len(seen_models),
            "filtered_non_tv": filtered_non_tv,
            "missing_pdp": missing_pdp,
            "duplicate_models": duplicate_models,
            "response_errors": response_errors,
            "api_reported_result_count": (
                max(api_total_records) if api_total_records else None
            ),
            "finder_info": finder_info_summaries,
        }

    def series_entries(self, open_dom):
        """入口 finder API → 按产品族分组的多尺寸型号 URL。

        API 的 modelList 比 JSON-LD 代表 URL 更完整；JSON-LD/DOM 仅在 API
        没有捕获到有效响应时回退，避免把一个尺寸误当成整个系列。
        """
        entry_meta: dict[str, object] = {}
        response_bodies: list[str] = []
        fallback_urls: dict[str, str] = {}
        html = ""
        visible_text = ""
        dom = None
        with open_dom(ENTRY, self.spec_entry_wait) as (res, dom, html):
            entry_meta = dict(getattr(res, "meta", {}) or {})
            response_bodies = list(getattr(res, "response_bodies", []) or [])
            if not res.ok or dom is None:
                self.last_entry_audit = {
                    "requested_url": ENTRY,
                    "termination_reason": "entry_failed",
                    "details": {
                        "status": res.status,
                        "error": res.error,
                        "block_reason": res.block_reason,
                        "response_count": len(response_bodies),
                    },
                }
                return []
            visible_text = dom.text("body") or ""
            for url in self._iter_itemlist_urls(html or ""):
                model = _model_from_samsung_url(url)
                if model:
                    fallback_urls[model] = (
                        url if url.startswith("http") else urljoin(_BASE, url)
                    )
            for href in dom.attr_list(
                "a[href*='/mx/tvs/'], a[href*='/mx/lifestyle-tvs/']",
                "href", limit=3000,
            ):
                clean = href.split("?")[0].split("#")[0]
                model = _model_from_samsung_url(clean)
                if model and not _NON_TV_URL_RE.search(clean):
                    fallback_urls.setdefault(
                        model,
                        clean if clean.startswith("http") else urljoin(_BASE, clean),
                    )
            eval_js = getattr(dom, "eval_js", None)
            if callable(eval_js):
                direct = eval_js(_FINDER_JS)
                if isinstance(direct, dict):
                    entry_meta["direct_finder_status"] = direct.get("status")
                    entry_meta["direct_finder_pages"] = len(direct.get("bodies") or [])
                    if direct.get("error"):
                        entry_meta["direct_finder_error"] = str(direct.get("error"))[:500]
                    direct_bodies = [
                        str(body) for body in (direct.get("bodies") or [])
                        if body
                    ]
                    response_bodies = list(
                        dict.fromkeys(direct_bodies + response_bodies)
                    )

        entries, api_stats = self._parse_finder_bodies(response_bodies, fallback_urls)
        source = "finder_api"
        if not entries:
            source = "jsonld_dom_fallback"
            fallback_models: dict[str, str] = {}
            for model, url in fallback_urls.items():
                if model not in fallback_models:
                    fallback_models[model] = url
            grouped: dict[str, list[str]] = {}
            for model, url in fallback_models.items():
                grouped.setdefault(_series_of(model), []).append(url)
            entries = list(grouped.items())

        reported_result_count = _reported_result_count(html, visible_text)
        discovered_models = sum(len(urls) for _, urls in entries)
        self.last_entry_audit = {
            "requested_url": ENTRY,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(entries),
            "discovered_model_count": discovered_models,
            "termination_reason": source,
            "details": {
                "source": source,
                "reported_result_count": reported_result_count,
                "response_count": len(response_bodies),
                "entry_meta": entry_meta,
                "fallback_url_count": len(fallback_urls),
                **api_stats,
            },
        }
        return entries

    def model_from_url(self, url: str) -> str:
        return _model_from_samsung_url(url) or url

    def classify_spec_empty(self, dom: Dom | None, series: str = "",
                             model: str = "", url: str = "") -> str:
        """区分 Samsung 产品页缺少规格区块与 accordion 未产生规格项。"""
        if dom is None:
            return "empty"
        section_count = dom.count("#specs.pdd32-product-spec") or dom.count("#specs")
        if section_count == 0:
            return "no_spec_section"
        if dom.count("#specs .pdd32-product-spec__content-item") == 0:
            return "spec_section_no_rows"
        return "spec_rows_unparsed"

    @staticmethod
    def _clean_spec_text(value: object) -> str:
        return " ".join(str(value or "").replace("\xa0", " ").split())

    def _accordion_spec_rows(self, dom: Dom) -> list[dict[str, str]]:
        """读取 Samsung pdd32 规格 accordion，使用 textContent 兼容折叠内容。"""
        eval_js = getattr(dom, "eval_js", None)
        if callable(eval_js):
            data = eval_js(
                """
                (() => {
                    const section = document.querySelector("#specs.pdd32-product-spec, #specs");
                    if (!section) return [];
                    const clean = (value) => (value || "").replace(/\\s+/g, " ").trim();
                    return Array.from(
                        section.querySelectorAll(".pdd32-product-spec__item")
                    ).flatMap((group) => {
                        const categoryNode = group.querySelector(
                            ".pdd32-product-spec__toggle-cta, " +
                            ".pdd32-product-spec__title, h3, h4"
                        );
                        const category = clean(categoryNode && categoryNode.textContent);
                        return Array.from(
                            group.querySelectorAll(".pdd32-product-spec__content-item")
                        ).map((item) => {
                            const title = item.querySelector(
                                ".pdd32-product-spec__content-item-title"
                            );
                            const desc = item.querySelector(
                                ".pdd32-product-spec__content-item-desc"
                            );
                            return {
                                category,
                                item: clean(title && title.textContent),
                                value: clean(desc && desc.textContent),
                            };
                        });
                    });
                })()
                """
            )
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]

        # 非 Playwright DOM 或页面结构轻微变化时的 CSS 回退。
        rows: list[dict[str, str]] = []
        groups = dom.sub(
            "#specs.pdd32-product-spec .pdd32-product-spec__item",
            limit=100,
        )
        if not groups:
            groups = dom.sub("#specs .pdd32-product-spec__item", limit=100)
        for group in groups:
            category = self._clean_spec_text(
                group.text(
                    ".pdd32-product-spec__toggle-cta, "
                    ".pdd32-product-spec__title, h3, h4"
                )
            )
            for item in group.sub(
                ".pdd32-product-spec__content-item", limit=300
            ):
                rows.append({
                    "category": category,
                    "item": self._clean_spec_text(
                        item.text(".pdd32-product-spec__content-item-title")
                    ),
                    "value": self._clean_spec_text(
                        item.text(".pdd32-product-spec__content-item-desc")
                    ),
                })
        return rows

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        """解析 Samsung 产品页的 pdd32 SPEC accordion，并保留 table 回退。"""
        from ...models import SpecCell, SpecRow

        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty

        sheet = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        seen: set[tuple[str, str]] = set()
        order = 0
        for raw in self._accordion_spec_rows(dom):
            category = self._clean_spec_text(raw.get("category"))
            key = self._clean_spec_text(raw.get("item"))
            value = self._clean_spec_text(raw.get("value"))
            if not (category or key or value):
                continue
            category = category or "Especificaciones"
            # Samsung 有少数只有 value 的项目（如 Tipo de producto=LED）。
            key = key or category
            pair = (category, key)
            if pair in seen:
                continue
            seen.add(pair)
            order += 1
            sheet.rows.append(SpecRow(
                category=category,
                item_ja=key,
                item_zh=translate(key),
                values=[SpecCell(model, value)] if value else [],
                order=order,
            ))
        if sheet.rows:
            return sheet

        # 兼容旧版/其他地区仍使用键值表的 Samsung 产品页。
        tables = dom.sub("section[class*='spec'] table", limit=1)
        if not tables:
            tables = dom.sub(".spec-list table, .specs table", limit=1)
        if not tables:
            tables = dom.sub("table", limit=1)
        if tables:
            return parse_en_kv_table(tables[0], self.code, self.name, series, model, url)
        return empty
