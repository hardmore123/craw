"""El Palacio de Hierro 墨西哥零售站适配器（价格 + 网评，同一商品页同步抓取）。

搜索页通过 JSON-LD ItemList 发现完整 .html PDP；价格与评价汇总在 PDP
的 Demandware meta/class 结构中，评价正文随页面同步抽取（若页面实际渲染）。
"""
from __future__ import annotations

import re
import urllib.parse

from ...extract import jsonld_blocks
from ...models import L2_MEDIUM, SearchHit
from .. import SiteAdapter


class PalacioMxAdapter(SiteAdapter):
    code = "palacio_mx"
    name = "El Palacio de Hierro"
    base_url = "https://www.elpalaciodehierro.com"
    country = "Mexico"
    channel = "Palacio"
    protection = L2_MEDIUM
    requires_browser = True
    supports_retail = True
    retail_page_wait = "h1"
    suggested_interval = 5.0

    def search_url(self, keyword: str, page: int = 1) -> str:
        q = urllib.parse.quote(str(keyword or "").strip())
        url = f"{self.base_url}/buscar?q={q}"
        if page and page > 1:
            url += f"&page={int(page)}"
        return url

    @staticmethod
    def _looks_like_tv(name: str) -> bool:
        low = (name or "").lower()
        if any(token in low for token in ("mueble de tv", "soporte para tv", "rack para tv", "mesa de tv")):
            return False
        return any(token in low for token in (
            "pantalla", "televisor", "smart tv", "qled", "oled", "mini led", "mini-led", "nanocell"
        ))

    def parse_search(self, dom, html: str = "", limit: int = 50) -> list[SearchHit]:
        """优先从搜索页 ItemList 取真实 .html PDP，避免猜测商品卡片 DOM。"""
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for block in jsonld_blocks(html or ""):
            typ = block.get("@type") if isinstance(block, dict) else None
            types = typ if isinstance(typ, list) else [typ]
            elements = block.get("itemListElement") if isinstance(block, dict) else None
            if "ItemList" not in types and not elements:
                continue
            for entry in elements or []:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item") or entry
                if not isinstance(item, dict):
                    continue
                name = " ".join(str(item.get("name") or "").split())
                raw_url = str(item.get("url") or item.get("@id") or "").strip()
                if not raw_url or not name or not self._looks_like_tv(name):
                    continue
                url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", raw_url)
                if not url.lower().endswith(".html") or url in seen:
                    continue
                seen.add(url)
                try:
                    rank = int(entry.get("position") or len(hits) + 1)
                except (TypeError, ValueError):
                    rank = len(hits) + 1
                hits.append(SearchHit(sku=url, url=url, title=name, rank=rank))
                if len(hits) >= limit:
                    return hits

        # JSON-LD 改版或暂时缺失时保留一个低优先级 DOM 兜底；仍只接受真实 .html PDP。
        if dom is not None and len(hits) < limit:
            for block in dom.sub("a[href$='.html']", limit=max(limit * 3, limit)):
                raw_url = str(block.self_attr("href") or "").strip()
                url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", raw_url)
                title = " ".join(block.self_text().split())
                if not url.lower().endswith(".html") or url in seen or not self._looks_like_tv(title):
                    continue
                seen.add(url)
                hits.append(SearchHit(sku=url, url=url, title=title, rank=len(hits) + 1))
                if len(hits) >= limit:
                    break
        return hits

    def product_url(self, sku: str) -> str:
        # ItemList 当前返回完整 URL；同时兼容根相对 .html href。
        value = str(sku or "").strip()
        if value.startswith("http"):
            return value
        if value.startswith("/"):
            return urllib.parse.urljoin(self.base_url.rstrip("/") + "/", value.lstrip("/"))
        return urllib.parse.urljoin(self.base_url.rstrip("/") + "/", value)

    def reviews_url(self, sku: str, page: int = 1) -> str:
        return ""
