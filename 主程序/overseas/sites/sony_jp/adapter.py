"""索尼 BRAVIA 日本 TV 官网 SPEC 适配器。

实测结构（2026-09）：
- lineup 入口 https://www.sony.jp/bravia/lineup/ 列出在售系列产品链接
  /bravia/products/<型号>/（如 K-XR90M2、KJ-X81L、XRJ-A95L）
- SPEC 页 = /bravia/products/<型号>/spec.html，页内单个 <table>
- 表是纵向单列：区分行（th colspan、无 td）+ 项目行（th + 单 td，全系列共用值）
  +「型」行（td 内用【型号】尺寸罗列该系列各机型）
- 解析交给 spec_parser.parse_vertical_spec_table

只做 SPEC 采集，不支持关键词搜索/评价。
"""
from __future__ import annotations

import re

from ...models import L1_MILD, SpecSheet
from ...spec_parser import parse_vertical_spec_table
from ...fetchers import Dom
from .. import SiteAdapter

# /bravia/products/<型号>/  型号形如 K-XR90M2 / KJ-X81L / XRJ-A95L
_PROD_RE = re.compile(r'href="(/bravia/products/([A-Za-z0-9\-]+)/)"', re.I)


class SonyJpAdapter(SiteAdapter):
    code = "sony_jp"
    name = "Sony BRAVIA（日本）"
    base_url = "https://www.sony.jp"
    country = "日本"
    channel = "Sony 官网"
    protection = L1_MILD
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    spec_entry_wait = "a[href*='/bravia/products/']"
    # lineup/gallery 页面由 JS 延迟注入产品卡片，需更长等待
    spec_entry_extra_wait_ms = 8000

    ENTRY = "https://www.sony.jp/bravia/gallery/"

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def list_series(self, dom: Dom | None, html: str = "") -> list[tuple[str, str]]:
        """从 lineup 页解析在售系列 → [(型号, SPEC页URL)]。

        列表靠 JS 渲染，优先用 DOM 读锚点 href（浏览器已补成绝对 URL），
        HTML 正则作兜底。型号从 /bravia/products/<型号>/ 里取。
        """
        hrefs: list[str] = []
        if dom is not None:
            hrefs = dom.attr_list("a[href*='/bravia/products/']", "href", limit=200)
        # 兜底：从原始 HTML 里补
        hrefs += re.findall(r'href="([^"]*bravia/products/[^"]*)"', html or "")

        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        pat = re.compile(r"/bravia/products/([A-Za-z0-9\-]+)/", re.I)
        for href in hrefs:
            m = pat.search(href or "")
            if not m:
                continue
            model = m.group(1)
            key = model.upper()
            if key in seen:
                continue
            seen.add(key)
            spec_url = f"{self.base_url}/bravia/products/{model}/spec.html"
            out.append((model, spec_url))
        return out

    def series_page_url(self, series: str) -> str:
        return f"{self.base_url}/bravia/products/{series}/"

    def spec_url(self, series: str) -> str:
        return f"{self.base_url}/bravia/products/{series}/spec.html"

    def parse_spec(self, dom: Dom | None, series: str, url: str = "") -> SpecSheet:
        if dom is None:
            return SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        tables = dom.sub("table", limit=1)
        if not tables:
            return SpecSheet(brand=self.code, brand_name=self.name, series=series, url=url)
        return parse_vertical_spec_table(tables[0], self.code, self.name, series, url)
