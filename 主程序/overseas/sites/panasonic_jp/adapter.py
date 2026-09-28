"""松下 VIERA（日本）TV 官网 SPEC 适配器（多级流程）。

实测结构（2026-09）：
- 产品总览 https://panasonic.jp/viera/products.html 列各系列页 /viera/<系列>.html
  （如 W97C / Z95C / W95C / W93C / Z95B / Z90B / W90B / W80C / MR770 / N50C）
- 系列页 /viera/<系列>.html 列该系列各尺寸型号的 SPEC 页
  /viera/products/TV-<尺寸><系列>/spec.html（也有 TH- 前缀的旧机型）
- SPEC 页 = 单表 table.c-prd007__table，多层 rowspan 分类结构，单机型单值。

一个系列有多个尺寸型号（各自一页），采集时抓齐后横排合并成一张多机型表。
走框架的多级 SPEC 流程：series_entries + parse_spec_model + model_from_url。
"""
from __future__ import annotations

import re

from ...models import L1_MILD, SpecSheet
from ...spec_parser import parse_panasonic_table
from ...fetchers import Dom
from .. import SiteAdapter

# 系列码：字母开头且必须含数字（如 W97C / Z95C / MR770 / N50C），
# 借此排除 PRODUCTS / OPTION / SUPPORT 等纯字母导航链接。
_SERIES_HREF = re.compile(r"/viera/([A-Z]{1,3}\d{2,4}[A-Z]?)\.html$", re.I)
_MODEL_HREF = re.compile(r"/viera/products/((?:TH|TV)-[0-9A-Za-z]+)/spec\.html", re.I)


class PanasonicJpAdapter(SiteAdapter):
    code = "panasonic_jp"
    name = "松下 VIERA（日本）"
    base_url = "https://panasonic.jp"
    country = "日本"
    channel = "Panasonic 官网"
    protection = L1_MILD
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/viera/']"
    # 总览系列链接由脚本延迟注入，入口额外等待后再读取完整列表。
    spec_entry_extra_wait_ms = 5000
    spec_page_wait = "table.c-prd007__table"
    # 松下页面完整加载很慢（分析脚本多），用 commit 尽早拿到 DOM，靠 wait_selector 等内容
    spec_wait_until = "commit"

    PRODUCTS = "https://panasonic.jp/viera/products.html"

    def spec_entry_url(self) -> str:
        return self.PRODUCTS

    # ---- 多级流程 ----
    def series_entries(self, open_dom):
        """products.html → 各系列页 → 各型号 SPEC 页 URL。"""
        # 1) 产品总览取系列页
        series_pages: list[tuple[str, str]] = []   # (系列名, 系列页URL)
        with open_dom(self.PRODUCTS, "a[href*='/viera/']") as (res, dom, html):
            if dom is None or not res.ok:
                return []
            hrefs = dom.attr_list("a[href*='/viera/']", "href", limit=400)
            hrefs += re.findall(r'href="([^"]*/viera/[^"]*)"', html or "")
            seen: set[str] = set()
            for h in hrefs:
                m = _SERIES_HREF.search(h or "")
                if not m:
                    continue
                code = m.group(1).upper()
                if code in seen:
                    continue
                seen.add(code)
                url = h if h.startswith("http") else self.base_url + h
                # 归一化到 https://panasonic.jp/viera/<系列>.html
                url = f"{self.base_url}/viera/{code}.html"
                series_pages.append((code, url))

        # 2) 逐系列页取型号 SPEC URL
        entries: list[tuple[str, list[str]]] = []
        for code, spurl in series_pages:
            model_urls: list[str] = []
            try:
                with open_dom(spurl, "a[href*='/viera/products/']") as (res, dom, html):
                    if dom is None or not res.ok:
                        continue
                    hrefs = dom.attr_list("a[href*='/viera/products/']", "href", limit=200)
                    hrefs += re.findall(r'href="([^"]*/viera/products/[^"]*)"', html or "")
                    seen_m: set[str] = set()
                    for h in hrefs:
                        m = _MODEL_HREF.search(h or "")
                        if not m:
                            continue
                        model = m.group(1).upper()
                        if model in seen_m:
                            continue
                        seen_m.add(model)
                        model_urls.append(
                            f"{self.base_url}/viera/products/{model}/spec.html")
            except Exception:
                pass
            if model_urls:
                entries.append((code, model_urls))
        return entries

    def model_from_url(self, url: str) -> str:
        m = _MODEL_HREF.search(url or "")
        return m.group(1).upper() if m else url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        tables = dom.sub("table.c-prd007__table", limit=1)
        if not tables:
            tables = dom.sub("table", limit=1)
        if not tables:
            return empty
        return parse_panasonic_table(tables[0], self.code, self.name, series, model, url)
