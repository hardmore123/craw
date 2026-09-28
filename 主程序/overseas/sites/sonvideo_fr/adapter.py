"""Son-Video 适配器：URL 规则 + 能力标记，字段抽取由 RetailSpec 驱动。

探测日期 2026-09-25：
- 首页 200，电视分类 /r/tv-televiseurs、/tv-televiseurs 均 404
- 搜索页 /recherche?q=televiseur 返回 200 但通用选择器匹配 0 产品
- 状态: blocked（站结构特殊，需定制 selector 或住宅IP + JS渲染验证）
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class SonvideoFrAdapter(SiteAdapter):
    code = "sonvideo_fr"
    name = "Son-Video"
    base_url = "https://www.son-video.com"
    country = "France"
    channel = "Son-Video"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    spec_driven_detail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/recherche?q={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        url = self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)
        return url

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return v
