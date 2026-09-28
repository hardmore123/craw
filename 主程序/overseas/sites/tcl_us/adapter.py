"""TCL 美国 TV 官网 SPEC 适配器（多级流程，Shopify 站）。

实测结构（2026-09，https://us.tcl.com/）：
- 入口使用 /collections/tv-home-theater?page=N 的 #product-grid > li 产品卡片，
  共显示 24 ITEMS，分页中同时包含电视和 Sound Bar、Projector、Party Speaker
  等非电视产品；不能扫描全页 /products/ 链接，否则会混入隐藏 mega-menu。
- 卡片标题/正文包含系列型号；URL 仅作为系列名的 fallback。A300W、Q3K、Q651G
  等 class/q-class URL 不能靠 slug 首段命名，必须优先使用卡片文本。
- 一个电视产品页 = 一个系列（如 QM8K），页面内含该系列所有尺寸型号
  （65/75/85/98QM8K），规格按型号分段（div.specifications_box），
  分组标题在 .specifications_one .h3，键值在 .specifications_two .specifications_time。
- 因此一个产品页即可解析出整系列多机型横排表；series_entries 每系列只给
  产品页 URL 一条，parse_spec_model 直接返回多机型表（外层合并对单表幂等）。

只做 SPEC；价格/网评由零售站适配器负责。
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser_us import parse_tcl_us_html
from ...fetchers import Dom
from .. import SiteAdapter

# 产品链接：/products/<slug>；去掉 ?variant= 查询
_PRODUCT_RE = re.compile(
    r"href=[\"'](?:https?://[^/\"']+)?(/products/[a-z0-9\-]+)", re.I)
_CARD_SELECTOR = "#product-grid > li"
# URL 可证明为电视的关键词；q-class 产品则依赖卡片文本中的型号/TV。
_TV_HINT = ("-tv", "smart-tv", "google-tv", "qled", "mini-led", "uhd",
            "nxtvision", "-series")
# 明确的非电视类型。故意不含 mount：A300W 的电视 URL 自带 flush-wall-mount。
_TV_EXCLUDE = ("monitor", "projector", "remote", "soundbar", "sound-bar",
               "party-speaker", "speaker", "subwoofer", "headphone", "earbud",
               "-cam", "camera", "glasses", "battery", "adapter", "cable",
               "stand-")
_NON_TV_PATTERNS = (
    ("soundbar", re.compile(r"sound\s*bar|soundbar", re.I)),
    ("projector", re.compile(r"projector", re.I)),
    ("party_speaker", re.compile(r"party\s+speaker", re.I)),
    ("speaker", re.compile(r"\bspeaker(?:s)?\b", re.I)),
    ("subwoofer", re.compile(r"subwoofer", re.I)),
    ("monitor", re.compile(r"\bmonitor\b", re.I)),
    ("remote", re.compile(r"\bremote\b", re.I)),
    ("headphone", re.compile(r"headphones?|earbuds?", re.I)),
    ("accessory", re.compile(r"\b(?:camera|glasses|battery|adapter|cable)\b", re.I)),
    ("stand", re.compile(r"\bstand\s+(?:for|with)\b|\btable\s+stand\b", re.I)),
)
# 从产品页标题/slug 提取系列码：如 qm8k-series → QM8K
_SERIES_FROM_SLUG = re.compile(r"/products/([a-z0-9]+)-series", re.I)
# 卡片标题中的型号码：Q3K/Q651G/QM8L/RM9L/A300W/X11K 等。
_MODEL_TOKEN_RE = re.compile(r"(?<![a-z0-9])([a-z]{1,5}\d[a-z0-9]{0,8})(?![a-z0-9])", re.I)
_MODEL_TOKEN_IGNORES = {
    "HDR10", "HDR10PLUS", "HDMI2", "ATSC3", "USB2", "USB3", "WIFI6",
    "CLASS", "INCH", "SERIES", "GOOGLE", "DOLBY",
}
_SIZE_RE = re.compile(
    r"(?<![a-z0-9])(\d{2,3})\s*(?:[\"”]|(?:-?\s*)inch(?:es)?\b|\bin\b)",
    re.I,
)


def _clean_product_path(value: str) -> str:
    value = str(value or "").split("?")[0].split("#")[0]
    match = re.search(r"/products/[a-z0-9\-]+", value, re.I)
    return match.group(0).lower() if match else ""


def _clean_card_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


class TclUsAdapter(SiteAdapter):
    code = "tcl_us"
    name = "TCL（美国）"
    base_url = "https://us.tcl.com"
    country = "USA"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 4.0
    supports_spec = True
    # 等待主产品网格，而不是隐藏 mega-menu 中的任意 product href。
    spec_entry_wait = _CARD_SELECTOR
    spec_page_wait = "body"

    ENTRY = "https://us.tcl.com/collections/tv-home-theater?page=1"
    ENTRY_FALLBACK = "https://us.tcl.com/"
    ENTRY_MAX_PAGES = 12
    # 官网当前口径：24 个集合商品卡，其中电视卡约 12 个；尺寸在产品页内展开。
    spec_entry_expected_series_count = 12
    # Shopify 集合页可能把产品卡片延迟到滚动后才写入 DOM。
    spec_entry_scroll_passes = 5
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = _CARD_SELECTOR
    spec_entry_scroll_max_passes = 20
    spec_entry_scroll_stable_rounds = 2
    spec_entry_extra_wait_ms = 1500

    def spec_entry_url(self) -> str:
        return self.ENTRY

    def _card_exclusion_reason(self, path: str, text: str) -> str:
        combined = f"{path} {text}"
        for reason, pattern in _NON_TV_PATTERNS:
            if pattern.search(combined):
                return reason
        return ""

    def _is_tv_card(self, path: str, text: str) -> bool:
        if self._card_exclusion_reason(path, text):
            return False
        low_path = path.lower()
        low_text = text.lower()
        if any(hint in low_path for hint in _TV_HINT):
            return True
        if re.search(r"\b(?:tv|television)\b", low_text, re.I):
            return True
        # 当前 Q651G 卡片的 URL 是 q-class-*，型号只在卡片文本中出现；
        # 没有文本时也只允许这个明确的电视集合前缀，避免回到全页误抓。
        return low_path.startswith("/products/q-class-")

    def _collect_product_cards(self, dom: Dom | None, html: str) -> tuple[list[dict], dict]:
        """读取主产品网格，返回卡片记录及来源/数量元数据。"""
        records: list[dict] = []
        seen_paths: set[str] = set()
        meta = {
            "source": "none",
            "grid_li_count": 0,
            "grid_li_without_product": 0,
        }
        if dom is not None:
            card_doms = dom.sub(_CARD_SELECTOR, limit=200)
            if card_doms:
                meta["source"] = "product_grid"
                meta["grid_li_count"] = len(card_doms)
                for card in card_doms:
                    hrefs = card.attr_list("a[href*='/products/']", "href", limit=20)
                    path = _clean_product_path(hrefs[0] if hrefs else "")
                    if not path:
                        meta["grid_li_without_product"] += 1
                        continue
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    text = _clean_card_text(card.self_text())
                    reason = self._card_exclusion_reason(path, text)
                    records.append({
                        "path": path,
                        "text": text,
                        "is_tv": not bool(reason) and self._is_tv_card(path, text),
                        "exclusion_reason": reason or (
                            "not_tv_hint" if not self._is_tv_card(path, text) else ""
                        ),
                        "sizes": self._sizes_from_text(text),
                    })
                return records, meta

        # 静态 fixture/页面改版没有主网格时才降级到当前 HTML 的 product href；
        # 它会明确记录 source=fallback_links，不能与主网格审计混为一谈。
        meta["source"] = "fallback_links"
        hrefs = []
        if dom is not None:
            hrefs += dom.attr_list("a[href*='/products/']", "href", limit=800)
        hrefs += _PRODUCT_RE.findall(html or "")
        for href in hrefs:
            path = _clean_product_path(href)
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            reason = self._card_exclusion_reason(path, "")
            records.append({
                "path": path,
                "text": "",
                "is_tv": not bool(reason) and self._is_tv_card(path, ""),
                "exclusion_reason": reason or (
                    "not_tv_hint" if not self._is_tv_card(path, "") else ""
                ),
                "sizes": [],
            })
        return records, meta

    def _sizes_from_text(self, text: str) -> list[int]:
        sizes = {int(value) for value in _SIZE_RE.findall(text or "")}
        # 卡片有时写成 65QM8K，而不是 65-inch；只取型号前的尺寸。
        for match in re.finditer(r"(?<![a-z0-9])(\d{2,3})(?=[a-z]{1,5}\d)",
                                 text or "", re.I):
            value = int(match.group(1))
            if 24 <= value <= 120:
                sizes.add(value)
        return sorted(sizes)

    def _collect_products(self, dom: Dom | None, html: str) -> list[str]:
        """兼容旧调用：只返回主网格中被判定为 TV 的产品路径。"""
        records, _ = self._collect_product_cards(dom, html)
        return [str(record["path"]) for record in records if record["is_tv"]]

    def _series_from_card(self, path: str, text: str) -> str:
        candidates: list[str] = []
        for token in _MODEL_TOKEN_RE.findall(text or ""):
            token = token.upper()
            if token in _MODEL_TOKEN_IGNORES or token.startswith(("HDR", "HDMI")):
                continue
            candidates.append(token)
        # 卡片文本优先：可修复 class-nxtvision...a300w 与 q-class-*。
        if candidates:
            # 同一标题可能同时有多个尺寸型号，优先最长、再保留首次出现。
            return sorted(dict.fromkeys(candidates), key=lambda item: (-len(item), candidates.index(item)))[0]
        return self._series_from_url(path)

    def _series_from_url(self, url: str) -> str:
        m = _SERIES_FROM_SLUG.search(url or "")
        if m:
            return m.group(1).upper()
        # 处理无 -series 的 slug（例如含 A300W 的 nxtvision 产品）。
        slug = re.search(r"/products/([^/?#]+)", url or "", re.I)
        if not slug:
            return ""
        tokens = []
        for token in _MODEL_TOKEN_RE.findall(slug.group(1)):
            token = token.upper()
            if token not in _MODEL_TOKEN_IGNORES and not token.startswith(("HDR", "HDMI")):
                tokens.append(token)
        if tokens:
            return tokens[-1]
        first = slug.group(1).split("-", 1)[0].upper()
        return "" if first in {"CLASS", "Q", "TCL"} else first

    def _next_page_url(self, dom: Dom | None, current_url: str) -> str:
        """从集合页链接中取当前页之后最小的 page 参数。"""
        if dom is None:
            return ""
        current_match = re.search(r"[?&]page=(\d+)", current_url, re.I)
        current_page = int(current_match.group(1)) if current_match else 1
        hrefs = dom.attr_list("a[href*='page=']", "href", limit=80)
        candidates: list[tuple[int, str]] = []
        for href in hrefs:
            if not href:
                continue
            url = urljoin(current_url, href)
            match = re.search(r"[?&]page=(\d+)", url, re.I)
            if not match:
                continue
            page = int(match.group(1))
            if page > current_page:
                candidates.append((page, url))
        if not candidates:
            return ""
        return min(candidates, key=lambda item: item[0])[1]

    def _item_count(self, dom: Dom | None, html: str) -> int | None:
        text = dom.self_text() if dom is not None else ""
        text = text or html or ""
        counts = [int(value) for value in re.findall(r"\b(\d+)\s+ITEMS?\b", text, re.I)]
        return max(counts) if counts else None

    def series_entries(self, open_dom):
        """遍历 TCL 两页集合卡片，过滤非电视后按系列去重。"""
        all_tv_paths: list[str] = []
        seen_tv_paths: set[str] = set()
        all_records: list[dict] = []
        all_pages: list[dict] = []
        excluded_by_reason: dict[str, int] = {}
        expected_item_count: int | None = None
        used_url = self.ENTRY
        used_source = "none"
        termination = "entry_failed"

        # 只有主集合页完全无法得到产品卡时才尝试首页回退。
        for start_url in (self.ENTRY, self.ENTRY_FALLBACK):
            page_url = start_url
            visited: set[str] = set()
            found_cards = False
            start_page_count = len(all_pages)
            for page_index in range(self.ENTRY_MAX_PAGES):
                if page_url in visited:
                    termination = "page_cycle"
                    break
                visited.add(page_url)
                page_info: dict = {"url": page_url, "page": page_index + 1}
                try:
                    with open_dom(page_url, self.spec_entry_wait) as (res, dom, html):
                        page_info["status"] = res.status
                        page_info["meta"] = dict(getattr(res, "meta", {}) or {})
                        if dom is None or not res.ok:
                            page_info["reason"] = res.error or res.block_reason or str(res.status)
                            all_pages.append(page_info)
                            termination = "entry_failed" if page_index == 0 else "page_failed"
                            break
                        records, card_meta = self._collect_product_cards(dom, html)
                        all_records.extend(records)
                        page_info.update(card_meta)
                        page_info["product_card_count"] = len(records)
                        page_info["tv_card_count"] = sum(1 for record in records if record["is_tv"])
                        page_info["excluded_card_count"] = sum(
                            1 for record in records if not record["is_tv"])
                        page_info["item_count"] = self._item_count(dom, html)
                        if page_info["item_count"] is not None:
                            expected_item_count = max(
                                expected_item_count or 0, int(page_info["item_count"]))
                        page_info["excluded_by_reason"] = {}
                        for record in records:
                            if record["is_tv"]:
                                continue
                            reason = str(record["exclusion_reason"] or "not_tv")
                            page_info["excluded_by_reason"][reason] = (
                                page_info["excluded_by_reason"].get(reason, 0) + 1)
                            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
                        page_info["tv_paths"] = [record["path"] for record in records
                                                  if record["is_tv"]]
                        next_url = self._next_page_url(dom, page_url)
                    all_pages.append(page_info)
                except Exception as exc:
                    page_info["error"] = f"{type(exc).__name__}: {exc}"
                    all_pages.append(page_info)
                    termination = "entry_exception" if page_index == 0 else "page_exception"
                    break

                if records:
                    found_cards = True
                    used_url = start_url
                    used_source = str(page_info.get("source") or "none")
                for record in records:
                    if not record["is_tv"]:
                        continue
                    path = str(record["path"])
                    if path not in seen_tv_paths:
                        seen_tv_paths.add(path)
                        all_tv_paths.append(path)

                if not next_url:
                    termination = "no_next_page"
                    break
                if next_url in visited:
                    termination = "page_cycle"
                    break
                if page_index + 1 >= self.ENTRY_MAX_PAGES:
                    termination = "max_pages"
                    break
                page_url = next_url
            else:
                termination = "max_pages"

            if found_cards:
                break
            # 当前入口完全失败/没有卡片，开始回退；保留前一次页面审计。
            if len(all_pages) == start_page_count:
                termination = "entry_failed"

        groups: dict[str, dict] = {}
        unclassified: list[dict] = []
        card_by_path: dict[str, dict] = {}
        for record in all_records:
            card_by_path.setdefault(str(record.get("path") or ""), record)

        # 以全局电视路径为准；系列名优先使用卡片文本，再回退到 URL。
        for path in all_tv_paths:
            record = card_by_path.get(path, {"path": path, "text": "", "sizes": []})
            series = self._series_from_card(path, str(record.get("text") or ""))
            if not series:
                unclassified.append({"path": path, "text": record.get("text", "")})
                continue
            group = groups.setdefault(series, {
                "url": self.base_url + path,
                "paths": [],
                "sizes": set(),
                "texts": [],
            })
            if path not in group["paths"]:
                group["paths"].append(path)
            group["sizes"].update(int(size) for size in record.get("sizes") or [])
            text = str(record.get("text") or "")
            if text and text not in group["texts"]:
                group["texts"].append(text[:300])

        entries: list[tuple[str, list[str]]] = []
        details_series: list[dict] = []
        for series, info in groups.items():
            url = str(info["url"])
            entries.append((series, [url]))
            details_series.append({
                "series": series,
                # TCL 一个代表产品页会展开多尺寸；不要把系列码伪装成 models，
                # 否则 spec_export 会错误过滤掉产品页解析出的真实型号。
                "models": [],
                "urls": [url],
                "sizes": sorted(info["sizes"]),
                "card_paths": list(info["paths"]),
                "card_text": list(info["texts"]),
            })

        product_card_count = sum(int(page.get("product_card_count") or 0)
                                 for page in all_pages)
        tv_card_count = sum(int(page.get("tv_card_count") or 0)
                            for page in all_pages)
        excluded_card_count = sum(int(page.get("excluded_card_count") or 0)
                                  for page in all_pages)
        self.last_entry_audit = {
            "requested_url": used_url,
            "fallback_urls": [self.ENTRY, self.ENTRY_FALLBACK],
            "expected_series_count": self.spec_entry_expected_series_count,
            "discovered_series_count": len(entries),
            # 这里是 TV 代表产品页数量，不是产品页内展开的所有尺寸型号。
            "discovered_model_count": len(all_tv_paths),
            "termination_reason": termination,
            "details": {
                "expected_item_count": expected_item_count,
                "product_card_count": product_card_count,
                "tv_card_count": tv_card_count,
                "excluded_card_count": excluded_card_count,
                "excluded_by_reason": excluded_by_reason,
                "page_count": len(all_pages),
                "source": used_source,
                "pages": all_pages,
                "unclassified_tv_cards": unclassified,
                "series": details_series,
            },
        }
        return entries

    def model_from_url(self, url: str) -> str:
        return self._series_from_url(url)

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url)
        if dom is None:
            return empty
        html = dom.html()
        if not html:
            return empty
        return parse_tcl_us_html(html, self.code, self.name, series, url)
