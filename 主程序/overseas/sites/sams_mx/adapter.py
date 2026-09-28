"""Sam's Club 墨西哥零售站适配器（价格 + 网评，同一商品页同步抓取）。

站内搜索 → 商品详情页；价格与评价都在商品页，reviews_url 返回空串。
选择器/URL 为「以实测为准」初值。防护 L2，需浏览器。
"""
from __future__ import annotations

import urllib.parse

from ...models import L2_MEDIUM
from .. import SiteAdapter


class SamsMxAdapter(SiteAdapter):
    code = "sams_mx"
    name = "Sam's Club México"
    base_url = "https://www.sams.com.mx"
    country = "Mexico"
    channel = "Sam's"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    # ★ 不要给本站加 `block_marker_allowlist = ("captcha",)`！
    # 2026-09-20 实测踩过：本站整站在 PerimeterX 后面，挑战页标题是
    # "Verify Your Identity"、标记是 `px-captcha`——而 `px-captcha` **包含
    # `captcha` 子串**，一旦豁免 `captcha`，这个真实挑战就会被判成
    # `blocked=False`，进而被记成 `no_item`（静默丢数据，项目红线）。
    # 正确做法是让 PX 标题类标记生效（见 infra._BLOCK_MARKERS 的
    # "verify your identity" / "verifica tu identidad"），不在这里豁免。

    def search_url(self, keyword: str, page: int = 1) -> str:
        """站内搜索。路由是 `/search?q=`。

        ★ 2026-09-20 实测：原写法 `/busca?query=` 是**无效路由**（稳定 404），
        且把真实问题掩盖成了"搜索 404"。逐候选路径探测结果：

            `/busca?query=`  `/buscar?q=`  `/resultados?q=`  `/catalogsearch/result/`
                → 全部 404（无效路由）
            `/search?q=`  → 被站点**识别**（转入 PerimeterX 挑战，而非 404）

        `/search` 被识别而其余 404，说明它才是正确路由。
        注意本站**整站**在 PerimeterX 之后（首页返回的也是 "Verify Your Identity"），
        在拿到住宅/移动代理前无法端到端验证搜索结果结构。
        """
        q = urllib.parse.urlencode({"q": keyword.strip()})
        url = f"{self.base_url}/search?{q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        if str(sku).startswith("http"):
            return sku
        return f"{self.base_url}/producto/{sku}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""
