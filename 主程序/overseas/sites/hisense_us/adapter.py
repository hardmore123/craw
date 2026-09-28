"""Hisense 美国 TV 官网 SPEC 适配器（多级流程，Wix 站，概要规格）。

实测结构（2026-09，https://www.hisense-usa.com/）：
- /category/televisions 显示 181 products，产品通过 Load More 动态追加，
  /category/televisions?page=2 也可能返回同一集合的不同分页视图。
- 产品链接为 /product-page/...；型号可能位于 slug 末尾，也可能出现
  ``tv43qd40r``、``43-class-s5-d``、``75u6sv-pro`` 等非统一写法。
- 电视分类页还会混入投影、音箱、空调等 /product-page/ 产品，必须按 URL
  类型排除后再计入 181 个电视型号。
- 规格由 Wix ProductSpecification 组件从后端 API 异步渲染，无头浏览器不填充，
  初始 HTML 无结构化规格也无 JSON-LD。可稳定获取的只有标题（尺寸/系列/类型）、
  URL 型号、营销描述里的零散规格词。
- 故产出「概要 SPEC」：型号/尺寸/系列/面板/分辨率/系统/刷新率/HDR/音频等，
  足以跑通与导出；完整规格待后续用带头浏览器或 Hisense 规格 API 增强。
- 同系列不同尺寸各一页，按系列归组横排合并。

只做 SPEC；价格/网评由零售站适配器负责。
"""
from __future__ import annotations

import re

from ...models import L1_MILD, SpecSheet
from ...spec_parser_us import parse_hisense_us, parse_hisense_us_fullspecs
from ...fetchers import Dom
from .. import SiteAdapter

_PRODUCT_RE = re.compile(
    r"href=[\"']([^\"']*/product-page/[a-z0-9\-]+)", re.I)
# 型号一般是尺寸+字母数字，允许末尾 -pro；在 tv43qd40r 中从最后的数字开始取。
_MODEL_RE = re.compile(
    r"(\d{2,3}[a-z][a-z0-9]*(?:-[a-z0-9]+)?)$", re.I)
# 43-class-s5-d 这类旧产品 URL 没有把完整型号连在末尾。
_CLASS_MODEL_RE = re.compile(
    r"(\d{2,3})-class-([a-z0-9]+(?:-[a-z0-9]+)?)$", re.I)
# 系列：slug 里 <code>-series 或 <code>-pro-series。
_SERIES_SLUG = re.compile(
    r"-([a-z]{1,3}\d{1,4}(?:-[a-z]{1,8})?)-series", re.I)
_MODEL_SPLIT = re.compile(r"^(\d{2,3})([a-z].*)$", re.I)
_NON_TV_TERMS = (
    ("projector", ("projector", "px4-pro", "xr10")),
    ("speaker", ("speaker", "soundbar", "party-thunder")),
    ("air_product", ("air-products", "air-conditioner", "dehumidifier", "portable-ac")),
)


class HisenseUsAdapter(SiteAdapter):
    code = "hisense_us"
    name = "Hisense（美国）"
    base_url = "https://www.hisense-usa.com"
    country = "USA"
    channel = "Hisense 官网"
    protection = L1_MILD
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/product-page/']"
    spec_page_wait = "body"
    # 概要规格来自标题/描述，需页面文本渲染出来
    spec_entry_extra_wait_ms = 4000
    # 产品页需点击「Full Specs」才渲染完整规格（ProductSpecification 容器）
    spec_click_texts = ["Full Specs"]

    ENTRY = "https://www.hisense-usa.com/category/televisions"
    ENTRY_MAX_PAGES = 12
    spec_entry_expected_model_count = 181
    # 入口页既有分页，也可能在当前页通过 Load More 追加产品。
    spec_entry_scroll_passes = 4
    spec_entry_click_selectors = [
        "[data-hook='load-more-button']",
        "button:has-text('Load More')",
        "button:has-text('Learn More')",
    ]
    spec_entry_click_repeats = 20
    spec_entry_click_wait_ms = 3000
    spec_entry_click_growth_selector = "a[href*='/product-page/']"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def _model_from_url(self, url: str) -> str:
        slug = (url or "").split("?")[0].rstrip("/")
        # 先匹配末尾的紧凑/带 -pro 型号；对 tv43qd40r 会从 43 开始命中。
        m = _MODEL_RE.search(slug)
        if m:
            return re.sub(r"-", "", m.group(1)).upper()
        # 旧 URL：...-43-class-s5-d → 43S5D。
        m = _CLASS_MODEL_RE.search(slug)
        if m:
            return (m.group(1) + re.sub(r"-", "", m.group(2))).upper()
        return ""

    def _non_tv_reason(self, path: str) -> str:
        low = (path or "").lower()
        for reason, terms in _NON_TV_TERMS:
            if any(term in low for term in terms):
                return reason
        return ""

    def _series_from(self, url: str, model: str) -> str:
        m = _SERIES_SLUG.search(url or "")
        if m:
            return m.group(1).replace("-", "").upper()
        ms = _MODEL_SPLIT.match(model or "")
        return ms.group(2).upper() if ms else (model or url)

    def _collect_records(self, dom: Dom | None, html: str) -> list[dict]:
        hrefs = []
        if dom is not None:
            hrefs += dom.attr_list("a[href*='/product-page/']", "href", limit=1000)
        hrefs += _PRODUCT_RE.findall(html or "")
        records: list[dict] = []
        seen_paths: set[str] = set()
        for h in hrefs:
            p = (h or "").split("?")[0].split("#")[0]
            m = re.search(r"/product-page/[a-z0-9\-]+", p, re.I)
            if not m:
                continue
            p = m.group(0).lower()
            if p in seen_paths:
                continue
            seen_paths.add(p)
            non_tv = self._non_tv_reason(p)
            if non_tv:
                records.append({"path": p, "model": "", "is_tv": False,
                                "reason": non_tv})
                continue
            model = self._model_from_url(p)
            if not model:
                records.append({"path": p, "model": "", "is_tv": False,
                                "reason": "model_unparsed"})
                continue
            records.append({"path": p, "model": model, "is_tv": True,
                            "reason": ""})
        return records

    def _collect(self, dom: Dom | None, html: str) -> list[str]:
        """兼容旧调用：只返回去除非电视后的产品路径。"""
        return [str(record["path"]) for record in self._collect_records(dom, html)
                if record["is_tv"]]

    def _item_count(self, dom: Dom | None, html: str) -> int | None:
        text = dom.self_text() if dom is not None else ""
        text = text or html or ""
        counts = [int(value) for value in re.findall(
            r"\b(\d+)\s+products?\b", text, re.I)]
        return max(counts) if counts else None

    def series_entries(self, open_dom):
        """遍历入口分页；每页打开时点击 Load More，再按系列归组。"""
        urls: list[str] = []
        seen_models: set[str] = set()
        all_records: list[dict] = []
        pages: list[dict] = []
        termination = "entry_failed"
        expected_count: int | None = None

        for page in range(1, self.ENTRY_MAX_PAGES + 1):
            page_url = f"{self.ENTRY}?page={page}"
            info: dict = {"page": page, "url": page_url}
            try:
                with open_dom(page_url, self.spec_entry_wait) as (res, dom, html):
                    info["status"] = res.status
                    info["meta"] = dict(getattr(res, "meta", {}) or {})
                    info["raw_link_count"] = (
                        dom.count("a[href*='/product-page/']") if dom is not None else 0)
                    if dom is None or not res.ok:
                        info["reason"] = res.error or res.block_reason or str(res.status)
                        pages.append(info)
                        termination = "entry_failed" if page == 1 else "page_failed"
                        break
                    records = self._collect_records(dom, html)
                    all_records.extend(records)
                    info["unique_product_path_count"] = len(records)
                    info["tv_candidate_count"] = sum(
                        1 for record in records if record["is_tv"])
                    info["excluded_by_reason"] = {}
                    for record in records:
                        if record["is_tv"]:
                            continue
                        reason = str(record["reason"] or "unknown")
                        info["excluded_by_reason"][reason] = (
                            info["excluded_by_reason"].get(reason, 0) + 1)
                    info["item_count"] = self._item_count(dom, html)
                    if info["item_count"] is not None:
                        expected_count = max(expected_count or 0,
                                             int(info["item_count"]))
                pages.append(info)
            except Exception as exc:
                info["error"] = f"{type(exc).__name__}: {exc}"
                pages.append(info)
                termination = "entry_exception" if page == 1 else "page_exception"
                break

            page_urls: list[str] = []
            added = 0
            for record in records:
                if not record["is_tv"]:
                    continue
                model = str(record["model"])
                if model in seen_models:
                    continue
                seen_models.add(model)
                path = str(record["path"])
                page_urls.append(path)
                urls.append(path)
                added += 1
            if not records or added == 0:
                termination = "no_models" if not records else "no_growth"
                break
        else:
            termination = "max_pages"

        groups: dict[str, list[tuple[int, str, str]]] = {}
        for path in urls:
            model = self._model_from_url(path)
            series = self._series_from(path, model)
            ms = _MODEL_SPLIT.match(model)
            size = int(ms.group(1)) if ms else 0
            groups.setdefault(series, []).append((size, model, path))
        entries: list[tuple[str, list[str]]] = []
        details_series: list[dict] = []
        for series, items in groups.items():
            items.sort(key=lambda x: x[0], reverse=True)
            item_urls = [
                (u if u.startswith("http") else self.base_url + u)
                for _, _, u in items
            ]
            entries.append((series, item_urls))
            details_series.append({
                "series": series,
                "models": [model for _, model, _ in items],
                "urls": item_urls,
                "sizes": [size for size, _, _ in items],
            })

        excluded_by_reason: dict[str, int] = {}
        for record in all_records:
            if record["is_tv"]:
                continue
            reason = str(record["reason"] or "unknown")
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
        self.last_entry_audit = {
            "requested_url": self.ENTRY,
            "expected_model_count": expected_count or self.spec_entry_expected_model_count,
            "discovered_model_count": len(urls),
            "discovered_series_count": len(entries),
            "termination_reason": termination,
            "details": {
                "expected_product_count": expected_count,
                "page_count": len(pages),
                "tv_model_count": len(urls),
                "raw_unique_product_path_count": len(all_records),
                "excluded_by_reason": excluded_by_reason,
                "pages": pages,
                "series": details_series,
            },
        }
        return entries

    def model_from_url(self, url: str) -> str:
        return self._model_from_url(url) or url

    # ProductSpecification 容器（点 Full Specs 后填充完整规格）
    _SPEC_BOX_JS = (
        "() => { const b = document.querySelector("
        "\"[class*='ProductSpecification' i]\"); return b ? (b.innerText||'') : ''; }"
    )

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url)
        if dom is None:
            return empty
        # 1) 优先：点击 Full Specs 后 ProductSpecification 容器的完整规格
        spec_text = ""
        try:
            spec_text = dom.eval_js(self._SPEC_BOX_JS) or ""
        except Exception:
            spec_text = ""
        if spec_text.strip():
            sheet = parse_hisense_us_fullspecs(spec_text, model, series,
                                               self.code, self.name, url)
            if sheet.rows:
                return sheet
        # 2) 回退：从标题/URL/描述提取概要规格
        title = ""
        try:
            title = dom.attr("meta[property='og:title']", "content") or ""
        except Exception:
            title = ""
        if not title:
            title = dom.text("h1") or dom.text("title")
        page_text = dom.self_text() or dom.html()
        return parse_hisense_us(title, page_text, model, series,
                                self.code, self.name, url)
