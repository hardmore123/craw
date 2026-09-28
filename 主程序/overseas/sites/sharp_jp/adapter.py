"""夏普 AQUOS（日本）TV 官网 SPEC 适配器。

实测结构（2026-09）：
- 产品入口 https://www.sharp.co.jp/aquos/lineup/
  列出 /aquos/products/<系列>/ 产品页；同一系列的尺寸变体通过 query 展示。
- SPEC 页统一为 /aquos/products/<系列>/spec/，页面内包含多个
  table.table-spec：第一张是共用基本规格，后续是按型号拆开的能耗和尺寸表。
- 因此采用「入口→系列 SPEC 页」的一页一系列流程，解析器按页面型号顺序把
  能耗、尺寸数据对齐到对应型号，导出时再横向合并重复项目。
"""
from __future__ import annotations

import re

from ...fetchers import Dom
from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_sharp_tables, sharp_extract_models
from .. import SiteAdapter

_SERIES_HREF = re.compile(
    r"/aquos/products/([a-z][a-z0-9]{1,10})(?:/|\?|#|$)", re.I)
_SERIES_CODE = re.compile(r"^[a-z]{1,4}\d[a-z0-9]*$", re.I)


class SharpJpAdapter(SiteAdapter):
    code = "sharp_jp"
    name = "夏普 AQUOS（日本）"
    base_url = "https://www.sharp.co.jp"
    country = "日本"
    channel = "夏普官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/aquos/products/']"
    # lineup 产品卡片延迟渲染，避免入口只拿到部分系列。
    spec_entry_extra_wait_ms = 5000
    spec_page_wait = "table.table-spec"

    ENTRY = "https://www.sharp.co.jp/aquos/lineup/"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def list_series(self, dom: Dom | None, html: str = "") -> list[tuple[str, str]]:
        """从电视产品入口提取系列码，并构造对应 SPEC 页。"""
        hrefs: list[str] = []
        if dom is not None:
            hrefs = dom.attr_list("a[href*='/aquos/products/']", "href", limit=600)
        hrefs += re.findall(
            r"href=[\"']([^\"']*/aquos/products/[^\"']*)", html or "", re.I)

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href in hrefs:
            match = _SERIES_HREF.search(href or "")
            if not match:
                continue
            code = match.group(1).lower()
            if not _SERIES_CODE.fullmatch(code) or code in seen:
                continue
            seen.add(code)
            out.append((code.upper(), f"{self.base_url}/aquos/products/{code}/spec/"))
        return out

    def parse_spec(self, dom: Dom | None, series: str, url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        if dom is None:
            return empty
        text = dom.self_text() or ""
        html = dom.html() or ""
        models = sharp_extract_models(text + "\n" + html, series)
        tables = dom.sub("table.table-spec", limit=200)
        if not tables:
            tables = dom.sub("table", limit=200)
        if not tables:
            return empty
        return parse_sharp_tables(tables, self.code, self.name, series, models, url)
