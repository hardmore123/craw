"""LG 美国 TV 官网 SPEC 适配器（多级流程，Next.js/Coveo 站）。

实测结构（2026-09，https://www.lg.com/us/tvs）：
- 页面统计显示 91 个结果；入口还可能通过 Show all/See more 触发后续 Coveo
  请求。当前以 Coveo 顶层 results 为完整型号来源，DOM/HTML 作为补充。
- 产品链接为 /us/tvs/lg-<model>-...；官方结果也可能包含
  /us/lifestyle-products/lg-...，是否属于当前 91 结果由 Coveo totalCount 决定，
  不在入口适配器中擅自删掉。
- 完整规格在初始 __NEXT_DATA__ 的 props.pageProps.productData.allInfo：
  [{subtitle 分组, tableData:[{term 项目, description 值}]}]（无需异步加载）。
- 一页一个型号；同系列不同尺寸各一页，按系列码归组横排合并。

只做 SPEC；价格/网评由零售站适配器负责。
"""
from __future__ import annotations

import json
import re

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser_us import parse_lg_us_html
from ...fetchers import Dom
from .. import SiteAdapter

# 产品链接：/us/tvs/lg-<model>-... 或同一 TV 分类下的 Lifestyle Screen
_PRODUCT_RE = re.compile(
    r"href=[\"'](?:https?://[^/\"']+)?(/us/(?:tvs|lifestyle-products)/lg-[a-z0-9\-]+)",
    re.I)
# Coveo 结果使用 JSON 顶层 results/raw.ec_uri_link 解析，避免递归 childResults。
_EXCLUDE = ("buying-guide", "discontinued", "accessor", "custom-installation",
            "discover-", "collections")
# 型号：可选 OLED 前缀 + 2-3 位尺寸 + 系列尾，如 OLED77C6HUP / 100QNED84BUA
_MODEL_RE = re.compile(r"^(oled)?(\d{2,3})([a-z0-9]+)$", re.I)
_ENTRY_LINK_SELECTOR = (
    "a[href*='/us/tvs/lg-'], "
    "a[href*='/us/lifestyle-products/lg-']"
)


class LgUsAdapter(SiteAdapter):
    code = "lg_us"
    name = "LG（美国）"
    base_url = "https://www.lg.com"
    country = "USA"
    channel = "LG 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/us/tvs/lg-']"
    spec_page_wait = "body"
    # Coveo 搜索响应提供当前页面之外的完整 TV 结果（实测 totalCount=91）。
    spec_entry_expected_model_count = 91
    spec_entry_extra_wait_ms = 5000
    spec_entry_scroll_passes = 6
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = _ENTRY_LINK_SELECTOR
    spec_entry_scroll_max_passes = 20
    spec_entry_scroll_stable_rounds = 2
    # 兼容页面出现 Show all/See more 时的动态结果扩展；当前页面可能没有该按钮。
    spec_entry_click_texts = ["Show all", "Show All"]
    spec_entry_click_selectors = [
        "button:has-text('Show all')", "button:has-text('Show All')",
        "a:has-text('Show all')", "a:has-text('Show All')",
    ]
    spec_entry_click_repeats = 4
    spec_entry_click_wait_ms = 3000
    spec_entry_click_growth_selector = _ENTRY_LINK_SELECTOR
    spec_entry_response_pattern = r"platform\.cloud\.coveo\.com/rest/search/v2"
    spec_entry_response_limit = 6_000_000

    ENTRY = "https://www.lg.com/us/tvs"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def _model_from_slug(self, path: str) -> str:
        # /us/tvs/lg-oled77c6hup-oled-4k-tv → oled77c6hup；
        # /us/lifestyle-products/lg-27lx6tyga-lifestyle-screen 同样纳入分类结果。
        m = re.search(r"/us/(?:tvs|lifestyle-products)/lg-([a-z0-9]+)",
                      (path or "").lower())
        return m.group(1) if m else ""

    def _parse_model(self, model: str):
        m = _MODEL_RE.fullmatch(model or "")
        if not m:
            return None, None
        prefix, size, tail = m.group(1) or "", m.group(2), m.group(3)
        series = (prefix + tail).upper()
        return int(size), series

    def _response_product_paths(self, body: str) -> list[str]:
        """只取 Coveo 顶层 results 的产品路径，避免把 childResults 变体放大。"""
        try:
            payload = json.loads(body or "")
        except (TypeError, ValueError) as exc:
            raise ValueError("LG Coveo 响应不是完整 JSON") from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("LG Coveo 响应缺少顶层 results")
        paths: list[str] = []
        for result in results:
            if not isinstance(result, dict):
                continue
            raw = result.get("raw")
            path = raw.get("ec_uri_link") if isinstance(raw, dict) else None
            if not isinstance(path, str) or not path:
                path = result.get("clickUri") or result.get("uri")
            if isinstance(path, str) and (
                    "/us/tvs/lg-" in path.lower()
                    or "/us/lifestyle-products/lg-" in path.lower()):
                paths.append(path)
        return paths

    def _collect(self, dom: Dom | None, html: str,
                 response_bodies: list[str] | None = None) -> list[tuple[str, str]]:
        """返回 [(型号, 产品页URL)]，合并 DOM、HTML 与 Coveo 顶层响应。"""
        hrefs = []
        if dom is not None:
            hrefs += dom.attr_list(_ENTRY_LINK_SELECTOR, "href", limit=1000)
        hrefs += _PRODUCT_RE.findall(html or "")
        for body in response_bodies or []:
            hrefs += self._response_product_paths(body)
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for h in hrefs:
            p = (h or "").split("?")[0].split("#")[0]
            m = re.search(r"/us/(?:tvs|lifestyle-products)/lg-[a-z0-9\-]+", p, re.I)
            if not m:
                continue
            p = m.group(0)
            low = p.lower()
            if any(x in low for x in _EXCLUDE):
                continue
            model = self._model_from_slug(p)
            if not model or model in seen:
                continue
            size, series = self._parse_model(model)
            if size is None or not (24 <= size <= 120):
                continue
            seen.add(model)
            url = p if p.startswith("http") else self.base_url + p
            out.append((model, url))
        return out

    def _series_details(self, groups: dict[str, list[tuple[int, str, str]]]) -> list[dict]:
        details: list[dict] = []
        for series, items in groups.items():
            items.sort(key=lambda x: x[0], reverse=True)
            details.append({
                "series": series,
                # 必须是真实 LG 型号字符串，供 spec_export 做当前入口范围过滤。
                "models": [model.upper() for _, model, _ in items],
                "urls": [url for _, _, url in items],
                "sizes": [size for size, _, _ in items],
            })
        return details

    def series_entries(self, open_dom):
        """收集 DOM/HTML 与 Coveo 全量结果，按系列码归组横排合并。"""
        expected_count = None
        response_bodies: list[str] = []
        response_result_counts: list[int] = []
        response_total_counts: list[int] = []
        response_errors: list[str] = []
        dom_tv_links = 0
        dom_lifestyle_links = 0
        show_all_text_present = False
        entry_status: dict = {}
        pairs: list[tuple[str, str]] = []

        try:
            with open_dom(self.ENTRY, self.spec_entry_wait) as (res, dom, html):
                entry_status = {
                    "status": res.status,
                    "blocked": res.blocked,
                    "block_reason": res.block_reason,
                    "error": res.error,
                    "meta": dict(getattr(res, "meta", {}) or {}),
                }
                if dom is not None:
                    dom_tv_links = dom.count("a[href*='/us/tvs/lg-']")
                    dom_lifestyle_links = dom.count(
                        "a[href*='/us/lifestyle-products/lg-']")
                    body_text = dom.self_text() or ""
                    show_all_text_present = bool(re.search(
                        r"\bshow\s+all\b", body_text, re.I))
                if dom is None or not res.ok:
                    self.last_entry_audit = {
                        "requested_url": self.ENTRY,
                        "expected_model_count": self.spec_entry_expected_model_count,
                        "discovered_model_count": 0,
                        "discovered_series_count": 0,
                        "termination_reason": "entry_failed",
                        "details": {"entry": entry_status},
                    }
                    return []
                response_bodies = list(getattr(res, "response_bodies", []) or [])
                if not response_bodies:
                    raise RuntimeError("LG Coveo 产品响应未捕获，拒绝以首屏结果代替全量")
                for body in response_bodies:
                    try:
                        payload = json.loads(body or "")
                        count = payload.get("totalCount") if isinstance(payload, dict) else None
                        results = payload.get("results") if isinstance(payload, dict) else None
                        if isinstance(count, int):
                            expected_count = max(expected_count or 0, count)
                            response_total_counts.append(count)
                        response_result_counts.append(
                            len(results) if isinstance(results, list) else 0)
                    except (TypeError, ValueError, AttributeError) as exc:
                        response_errors.append(f"{type(exc).__name__}: {exc}")
                pairs = self._collect(dom, html, response_bodies)
        except Exception as exc:
            self.last_entry_audit = {
                "requested_url": self.ENTRY,
                "expected_model_count": expected_count or self.spec_entry_expected_model_count,
                "discovered_model_count": len(pairs),
                "discovered_series_count": 0,
                "termination_reason": "entry_exception",
                "details": {
                    "entry": entry_status,
                    "response_total_counts": response_total_counts,
                    "response_result_counts": response_result_counts,
                    "response_errors": response_errors,
                    "dom_tv_links": dom_tv_links,
                    "dom_lifestyle_links": dom_lifestyle_links,
                    "show_all_text_present": show_all_text_present,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            }
            raise

        groups: dict[str, list[tuple[int, str, str]]] = {}
        for model, url in pairs:
            size, series = self._parse_model(model)
            if size is None or not series:
                continue
            groups.setdefault(series, []).append((size, model, url))
        for items in groups.values():
            items.sort(key=lambda x: x[0], reverse=True)
        discovered_count = sum(len(items) for items in groups.values())
        details_series = self._series_details(groups)
        count_matches = expected_count is not None and discovered_count == expected_count
        termination = "coveo_total_count" if count_matches else "coveo_count_mismatch"
        self.last_entry_audit = {
            "requested_url": self.ENTRY,
            "expected_model_count": expected_count or self.spec_entry_expected_model_count,
            "discovered_model_count": discovered_count,
            "discovered_series_count": len(groups),
            "termination_reason": termination,
            "details": {
                "entry": entry_status,
                "response_total_counts": response_total_counts,
                "response_result_counts": response_result_counts,
                "response_count": len(response_bodies),
                "response_errors": response_errors,
                "dom_tv_links": dom_tv_links,
                "dom_lifestyle_links": dom_lifestyle_links,
                "show_all_text_present": show_all_text_present,
                "series": details_series,
            },
        }
        if response_errors:
            raise RuntimeError("LG Coveo 响应存在解析错误：" + "; ".join(response_errors))
        if expected_count is None or discovered_count != expected_count:
            raise RuntimeError(
                f"LG Coveo 结果数量不一致：expected={expected_count} discovered={discovered_count}"
            )
        return [(series, [url for _, _, url in items])
                for series, items in groups.items()]

    def model_from_url(self, url: str) -> str:
        return (self._model_from_slug(url) or url).upper()

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url)
        if dom is None:
            return empty
        html = dom.html()
        if not html:
            return empty
        return parse_lg_us_html(html, self.code, self.name, series, model, url)
