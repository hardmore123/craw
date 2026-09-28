"""TCL 墨西哥官网 TV SPEC 适配器（多级流程）。

入口：TCL 墨西哥电视频道（https://www.tcl.com/mx/es 下的电视分类，实测常见路径
如 /mx/es/tvs 或 /mx/es/products/tv）。型号页规格在「Especificaciones」区块，
走通用键值表解析 parse_en_kv_table。TCL 日本站用 JSON 端点，墨西哥站以页面表为主，
选择器为「以实测为准」的初值。只做 SPEC，不支持搜索/评价。
"""
from __future__ import annotations

import re

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_table
from ...fetchers import Dom
from .. import SiteAdapter

_BASE = "https://www.tcl.com"
ENTRY = f"{_BASE}/mx/es/tvs"

# 实测（2026-09）：入口 /mx/es/tvs 产品链接形如 /mx/es/tvs/<型号slug>，
# 型号在末段，如 65qm7l / 98qm7l / qm7l / q6k / x11l / a400-pro / 50qm5k。
# 分类锚点是 /mx/es/tvs?<xxx>（带 ?），/mx/es/tvs/compare 是对比页，都要排除。
# 型号页规格由 React 异步渲染（初始 HTML 无规格表），但有 JSON-LD Product。
_PROD_RE = re.compile(r"/mx/es/tvs/([a-z0-9][a-z0-9\-]*)$", re.I)
_NON_MODEL = {"compare"}


def _clean_model(seg: str) -> str:
    """slug 末段 → 型号：大写、去连字符。65qm7l→65QM7L；a400-pro→A400PRO。"""
    return (seg or "").upper().replace("-", "")


def _looks_like_model(seg: str) -> bool:
    s = (seg or "").lower()
    if s in _NON_MODEL or not s:
        return False
    # TV 型号一般含字母+数字（qm7l/q6k/x11l/98qm7l/a400），排除纯词 slug。
    return bool(re.search(r"[a-z]", s) and re.search(r"\d", s))


def _series_of(model: str) -> str:
    """去掉前导尺寸数字作系列码。65QM7L→QM7L；QM7L→QM7L；A400PRO→A400PRO。"""
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


class TclMxAdapter(SiteAdapter):
    code = "tcl_mx"
    name = "TCL（墨西哥）"
    base_url = _BASE
    country = "墨西哥"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a[href*='/mx/es/tvs/']"
    spec_entry_extra_wait_ms = 4000
    # 入口无分页按钮，但产品卡会懒加载；滚动到链接集合稳定后再解析全量。
    spec_entry_scroll_passes = 4
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/mx/es/tvs/']"
    spec_entry_scroll_max_passes = 20
    spec_entry_scroll_stable_rounds = 2
    spec_entry_expected_model_count = 50
    spec_entry_expected_series_count = 31
    # 规格由 React 异步渲染，等一个稳定容器；等不到也不熔断（parse 返回空记 empty）。
    spec_page_wait = "#pdp-root, main, body"

    def spec_entry_url(self) -> str:
        return ENTRY

    def series_entries(self, open_dom):
        with open_dom(ENTRY, self.spec_entry_wait) as (res, dom, html):
            if dom is None or not res.ok:
                self.last_entry_audit = {
                    "requested_url": ENTRY,
                    "termination_reason": "entry_failed",
                    "details": {"status": res.status, "error": res.error,
                                "block_reason": res.block_reason},
                }
                return []
            hrefs = dom.attr_list("a[href*='/mx/es/tvs/']", "href", limit=1200)
            hrefs += re.findall(r'href="([^"?#]*/mx/es/tvs/[a-z0-9][a-z0-9\-]*)"',
                                html or "", re.I)

        model_url: dict[str, str] = {}
        for h in hrefs:
            base = (h or "").split("?")[0].split("#")[0].rstrip("/")
            m = _PROD_RE.search(base)
            if not m:
                continue
            seg = m.group(1)
            if not _looks_like_model(seg):
                continue
            model = _clean_model(seg)
            if model in model_url:
                continue
            url = base if base.startswith("http") else _BASE + base
            model_url[model] = url

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": ENTRY,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": "scroll_stable",
            "details": {
                "raw_href_count": len(hrefs),
                "unique_model_count": len(model_url),
                "series_sizes": {series: len(urls)
                                 for series, urls in series_map.items()},
            },
        }
        return [(series, urls) for series, urls in series_map.items()]

    def model_from_url(self, url: str) -> str:
        base = (url or "").split("?")[0].split("#")[0].rstrip("/")
        m = _PROD_RE.search(base)
        return _clean_model(m.group(1)) if m else url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        """解析 TCL MX 官方公开规格 JSON，并保留页面表格回退。

        型号页会在浏览器上下文中加载同源
        ``specifications1/product_spec.<base64-path>.json``。端点 URL 随当前
        尺寸型号变化，因此从页面性能资源中读取，不跨地区拼接或猜测端点。
        """
        import json
        from ...spec_parser import parse_en_kv_pairs

        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty

        eval_js = getattr(dom, "eval_js", None)
        if callable(eval_js):
            try:
                resources = eval_js(
                    "() => performance.getEntriesByType('resource').map(entry => entry.name)"
                ) or []
                endpoint = next(
                    str(resource) for resource in resources
                    if "/specifications1/product_spec." in str(resource)
                    and str(resource).lower().endswith(".json")
                )
                raw = eval_js(
                    "async () => {"
                    f"const response = await fetch({json.dumps(endpoint)}, "
                    "{credentials: 'same-origin'});"
                    "if (!response.ok) return '';"
                    "return await response.text();"
                    "}"
                )
                payload = json.loads(raw) if isinstance(raw, str) else raw
                data = payload.get("data") if isinstance(payload, dict) else []
                triples: list[tuple[str, str, str]] = []
                for section in data if isinstance(data, list) else []:
                    if not isinstance(section, dict):
                        continue
                    category = str(section.get("tab") or "").strip()
                    for item in section.get("specItems") or []:
                        if not isinstance(item, dict):
                            continue
                        key = str(item.get("name") or "").strip()
                        value = str(item.get("value") or "").strip()
                        if key:
                            triples.append((category, key, value))
                if triples:
                    sheet = parse_en_kv_pairs(
                        triples, self.code, self.name, series, model, url
                    )
                    if sheet.rows:
                        return sheet
            except Exception:
                # 页面端点缺失或响应格式变化时继续尝试公开 DOM 表格，
                # 不把异常响应当作成功规格，也不改变保护检测结果。
                pass

        for sel in ("section[class*='spec'] table", ".specification table",
                    ".param table", "table"):
            tables = dom.sub(sel, limit=1)
            if tables:
                sh = parse_en_kv_table(tables[0], self.code, self.name, series, model, url)
                if sh.rows:
                    return sh
        return empty
