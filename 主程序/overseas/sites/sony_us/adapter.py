"""Sony 美国 TV 官网 SPEC 适配器（多级流程，Hybris/SPA 站）。

实测结构（2026-09，https://electronics.sony.com/）：
- TV 列表页 /tv-video/televisions/c/all-tvs 含各型号链接 /.../p/<model>
  （model 如 k65xr90m2 = K + 尺寸65 + 系列码 XR90M2）。
- 列表页内嵌状态 JSON 还包含每个系列的其它尺寸型号，字段常见为
  code/globalModelName/gwModel/gwSku/modelName/name/slug；这些变体没有
  全部对应的 DOM href，入口发现必须同时读取 JSON。
- 产品页规格在内嵌产品状态 JSON 的 "classifications" 里：
  [{code,name,features:[{code,name,featureValues:[{value}]}]}]。
- 同一系列（如 XR90M2）有多个尺寸型号，各自一页；按系列码归组，逐型号
  抓 JSON 后横排合并成多机型表。

只做 SPEC；价格/网评由零售站适配器负责。
"""
from __future__ import annotations

import re
from html import unescape as _html_unescape

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser_us import parse_sony_us_html
from ...fetchers import Dom
from .. import SiteAdapter

# 产品链接：.../p/<model>；model 形如 k65xr90m2 / k65a60 / xr65a95l
_PRODUCT_RE = re.compile(r'href=["\']([^"\']*/p/[a-z0-9]+)', re.I)
# 型号 → (尺寸, 系列码)：支持 K/KD/XR 前缀；系列从尺寸后的尾段取。
_MODEL_RE = re.compile(r"^(kd|xr|k)?(\d{2,3})([a-z0-9]+)$", re.I)
# 列表状态 JSON 中承载真实尺寸型号的字段。排除 code/name，避免把
# ``XR90M2_base``、``43” & below``、``120Hz`` 等筛选元数据当成产品型号。
_MODEL_FIELD_RE = re.compile(
    r"[\"'](?:globalModelName|gwModel|gwSku|modelName|slug)"
    r"[\"']\s*:\s*[\"']([^\"']+)[\"']", re.I)
# 处理字段值中带路径、前缀文字或转义分隔符的型号。
_EMBEDDED_MODEL_RE = re.compile(
    r"(?<![a-z0-9])(?:(?:kd|xr|k)[\s_-]*)?"
    r"\d{2,3}[\s_-]*[a-z][a-z0-9_-]{1,15}(?![a-z0-9])", re.I)


class SonyUsAdapter(SiteAdapter):
    code = "sony_us"
    name = "Sony（美国）"
    base_url = "https://electronics.sony.com"
    country = "USA"
    channel = "Sony 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/p/']"
    spec_page_wait = "body"

    ENTRY = "https://electronics.sony.com/tv-video/televisions/c/all-tvs?currentPage=1"
    ENTRY_MAX_PAGES = 12
    spec_entry_scroll_passes = 4
    spec_entry_extra_wait_ms = 1500
    # 官网 All TVs 当前入口为 17 个系列；这是审计参考值，不替代实际发现清单。
    spec_entry_expected_series_count = 17

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def _parse_model(self, model: str):
        compact = re.sub(r"[^a-z0-9]", "", str(model or "").lower())
        m = _MODEL_RE.fullmatch(compact)
        if not m:
            return None, None, None
        _, size, tail = m.group(1) or "", m.group(2), m.group(3)
        # K/KD/XR 都是型号前缀，系列码是尺寸后的尾段：
        # k65xr90m2 → XR90M2，xr77a95l → A95L，kd32w830k → W830K。
        series = tail.upper()
        return int(size), series, compact.upper()

    def _embedded_model_values(self, html: str) -> list[str]:
        """从入口页内嵌状态 JSON 取型号字段，避免只依赖可见代表 href。"""
        text = _html_unescape(html or "")
        values: list[str] = []
        for value in _MODEL_FIELD_RE.findall(text):
            value = value.replace(r"\/", "/").replace(r"\u002d", "-")
            # 直接字段通常就是型号；复杂字段再从其中提取紧凑型号。
            values.append(value)
            values.extend(_EMBEDDED_MODEL_RE.findall(value))
        return values

    def _model_candidates(self, value: str) -> list[str]:
        value = _html_unescape(str(value or "")).strip()
        if not value:
            return []
        candidates = [value]
        # href/slug 或带说明文字的 JSON 字段可能不是纯型号。
        candidates.extend(_EMBEDDED_MODEL_RE.findall(value))
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            compact = re.sub(r"[^a-z0-9]", "", candidate.lower())
            size, series, model = self._parse_model(compact)
            if size is None or not (24 <= size <= 120) or not series:
                continue
            if model not in seen:
                seen.add(model)
                out.append(model.lower())
        return out

    def _collect_models(self, dom: Dom | None, html: str) -> list[str]:
        """合并 DOM href 与入口页内嵌 JSON 的完整尺寸型号。"""
        values: list[str] = []
        if dom is not None:
            values += dom.attr_list("a[href*='/p/']", "href", limit=800)
        values += _PRODUCT_RE.findall(html or "")
        values += self._embedded_model_values(html)

        models: list[str] = []
        seen: set[str] = set()
        for value in values:
            for mdl in self._model_candidates(value):
                if mdl in seen:
                    continue
                seen.add(mdl)
                models.append(mdl)
        return models

    def series_entries(self, open_dom):
        """遍历 All TVs 分页，合并 JSON 尺寸变体后按系列码归组。"""
        models: list[str] = []
        seen_models: set[str] = set()
        page_audit: list[dict] = []
        termination = "entry_failed"

        for page in range(1, self.ENTRY_MAX_PAGES + 1):
            page_url = f"{self.ENTRY.split('?')[0]}?currentPage={page}"
            page_info: dict = {"page": page, "url": page_url}
            try:
                with open_dom(page_url, self.spec_entry_wait) as (res, dom, html):
                    page_info["status"] = res.status
                    page_info["meta"] = dict(getattr(res, "meta", {}) or {})
                    if dom is None or not res.ok:
                        page_info["reason"] = res.error or res.block_reason or str(res.status)
                        page_audit.append(page_info)
                        termination = "entry_failed" if page == 1 else "page_failed"
                        break
                    page_models = self._collect_models(dom, html)
                    page_info["model_count"] = len(page_models)
                    page_info["models"] = [self.model_from_url(
                        f"/p/{model}") for model in page_models]
            except Exception as exc:
                page_info["error"] = f"{type(exc).__name__}: {exc}"
                page_audit.append(page_info)
                termination = "entry_exception" if page == 1 else "page_exception"
                break

            page_audit.append(page_info)
            added = 0
            for model in page_models:
                if model not in seen_models:
                    seen_models.add(model)
                    models.append(model)
                    added += 1
            if not page_models:
                termination = "no_models"
                break
            if added == 0:
                termination = "no_growth"
                break
        else:
            termination = "max_pages"

        groups: dict[str, list[tuple[int, str]]] = {}
        for mdl in models:
            size, series, _ = self._parse_model(mdl)
            if size is None or not series:
                continue
            groups.setdefault(series, []).append((size, mdl))

        entries: list[tuple[str, list[str]]] = []
        details_series: list[dict] = []
        for series, items in groups.items():
            items.sort(key=lambda x: x[0], reverse=True)     # 尺寸降序
            urls = [f"{self.base_url}/tv-video/televisions/all-tvs/p/{m}"
                    for _, m in items]
            entries.append((series, urls))
            details_series.append({
                "series": series,
                # 这里必须是真实型号字符串；spec_export 会用它过滤当前周机型。
                "models": [m.upper() for _, m in items],
                "urls": urls,
                "sizes": [size for size, _ in items],
            })

        self.last_entry_audit = {
            "requested_url": self.ENTRY,
            "expected_series_count": self.spec_entry_expected_series_count,
            "discovered_series_count": len(entries),
            "discovered_model_count": len(models),
            "termination_reason": termination,
            "details": {
                "pages": page_audit,
                "page_count": len(page_audit),
                "embedded_variant_fields": True,
                "series": details_series,
            },
        }
        return entries

    def model_from_url(self, url: str) -> str:
        m = re.search(r"/p/([a-z0-9]+)", (url or "").lower())
        return m.group(1).upper() if m else url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url)
        if dom is None:
            return empty
        html = dom.html()
        if not html:
            return empty
        return parse_sony_us_html(html, self.code, self.name, series, model, url)
