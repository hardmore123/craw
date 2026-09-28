"""Falabella Peru adapter — Next.js + __NEXT_DATA__

实测（2026-09-27）：
- Cencosud 集团 Next.js 站，搜索页 /falabella-pe/search?Ntt=<kw>。
- PDP 含 __NEXT_DATA__ JSON（含 price/reviews）。
- 与 falabella_cl/falabella_co 同架构。
字段抽取由 RetailSpec（schemas/retail_specs/falabella_pe.retail.json）驱动。
"""
from __future__ import annotations

from ...models import L2_MEDIUM
from .. import SiteAdapter


class FalabellaPeAdapter(SiteAdapter):
    code = "falabella_pe"
    name = "Falabella Peru"
    base_url = "https://www.falabella.com.pe"
    country = "Peru"
    channel = "Falabella"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1, script[type='application/ld+json']"
    suggested_interval = 6.0
    SEARCH_PATH = "/falabella-pe/search?Ntt={q}"

    def search_url(self, keyword: str, page: int = 1) -> str:
        import urllib.parse
        q = urllib.parse.quote(keyword.strip())
        return self.base_url.rstrip("/") + self.SEARCH_PATH.format(q=q)

    def product_url(self, sku: str) -> str:
        v = str(sku or "").strip()
        if v.startswith("http"):
            return v
        if v.startswith("/"):
            return self.base_url.rstrip("/") + v
        return self.base_url.rstrip("/") + "/product/" + v
