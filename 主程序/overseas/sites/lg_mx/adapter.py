"""LG 墨西哥官网纯电视 SPEC 适配器（多级流程）。

入口：https://www.lg.com/mx/tv-soundbars/todas-las-tvs-y-soundbars/。
该入口同时含电视、TV+Soundbar 组合和精选轮播；适配器只读取真实分页结果卡，
按卡片标题排除 Soundbar/音响组合，再进入电视型号页。
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_table
from ...fetchers import Dom
from .. import SiteAdapter

_BASE = "https://www.lg.com"
ENTRY = f"{_BASE}/mx/tv-soundbars/todas-las-tvs-y-soundbars/?ec_model_status_code=Active"
ENTRY_MAX_PAGES = 12

# 产品链接是「分类/型号」两级路径，如：
# /mx/tv-soundbars/oled-evo/oled55c6psa/
_PROD_RE = re.compile(
    r"/mx/tv-soundbars/(?:[a-z0-9-]+/)*((?:lg-)?[a-z]*\d{2}[a-z0-9-]+)/?$",
    re.I,
)
_TV_MODEL_RE = re.compile(r"[A-Za-z]*\d{2,3}[A-Za-z]", re.I)
_CARD_SELECTOR = ".c-result-area__product-list .neo-card"
_NON_TV_CARD_RE = re.compile(
    r"\bcombo\b|soundbar|barra\s+de\s+sonido|speaker|bocina|"
    r"home[- ]?theater",
    re.I,
)
_NON_MODEL_SEGMENT_RE = re.compile(
    r"pulgadas|todas-las|smart-tvs|tv-de-|4k-uhd|combos|signature|"
    r"home-theater|buying-guide|wallpaper",
    re.I,
)
_RESULT_COUNT_RE = re.compile(r"(?<!\d)(\d{1,4})\s+Resultados?\b", re.I)


def _reported_result_count(html: str, visible_text: str) -> int | None:
    """提取入口正文结果数，优先使用用户可见文本而不是隐藏配置/JSON。"""
    visible = [
        int(value) for value in _RESULT_COUNT_RE.findall(visible_text or "")
    ]
    if visible:
        return max(visible)
    plain_html = unescape(re.sub(r"<[^>]*>", " ", html or ""))
    candidates = [
        int(value) for value in _RESULT_COUNT_RE.findall(plain_html)
    ]
    return max(candidates) if candidates else None


def _series_of(model: str) -> str:
    s = re.sub(r"\d{2,3}", "", model or "", count=1).upper()
    return s or (model or "").upper()


def _clean_url(value: str) -> str:
    return (value or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")


def _card_product(card: Dom) -> tuple[str, str]:
    """从一个真实结果卡返回 (规范型号, 绝对 URL)，非电视返回空。"""
    text = " ".join((card.self_text() or "").split())
    if _NON_TV_CARD_RE.search(text):
        return "", ""
    for href in card.attr_list("a", "href", limit=30):
        if "/mx/tv-soundbars/" not in href or "#pdp-review" in href:
            continue
        base = _clean_url(href)
        match = _PROD_RE.search(base)
        if not match:
            continue
        segment = base.rsplit("/", 1)[-1].lower()
        if _NON_MODEL_SEGMENT_RE.search(segment):
            continue
        model = re.sub(r"[^A-Za-z0-9]", "", match.group(1)).upper()
        if not _TV_MODEL_RE.search(model):
            continue
        return model, urljoin(_BASE, base)
    return "", ""


def _next_page_url(dom: Dom | None, html: str = "") -> str:
    """读取 LG 分页的「Página siguiente」，不误点产品轮播的 Next slide。"""
    # 浏览器 DOM 能可靠区分最后一页的 disabled 链接；HTML 中的 href 仍可能
    # 保留 &amp;，因此只在 DOM 接口不可用时走正则回退。
    if dom is not None and hasattr(dom, "eval_js"):
        js_checked = False
        try:
            js_checked = True
            href = dom.eval_js("""() => {
                const links = [...document.querySelectorAll('a')];
                const isNext = (element) => {
                    const text = ((element.getAttribute('aria-label') || '') + ' ' +
                                  (element.innerText || element.textContent || ''))
                                  .replace(/\\s+/g, ' ').trim();
                    return /p[aá]gina\\s+siguiente/i.test(text);
                };
                const link = links.find((element) => isNext(element) &&
                    (element.getAttribute('aria-disabled') || '').toLowerCase() !== 'true' &&
                    !element.hasAttribute('disabled'));
                return link ? (link.href || link.getAttribute('href') || '') : '';
            }""")
            if href:
                return urljoin(_BASE, unescape(str(href)))
        except Exception:
            pass
        if js_checked:
            return ""

    # 静态 DOM/旧实现回退：按完整 <a> 标签检查 aria-label 与 disabled 属性。
    next_label = r"p(?:[aá]|&aacute;|&#225;|&#xE1;)gina\s+siguiente"
    for anchor in re.findall(r"<a\b[^>]*>", html or "", re.I):
        if not re.search(r'''aria-label=["']''' + next_label + r'''["']''', anchor, re.I):
            continue
        if re.search(r'''aria-disabled=["']true["']|\sdisabled(?:\s*=|\s|>)''', anchor, re.I):
            continue
        match = re.search(r'''href=["']([^"']+)''', anchor, re.I)
        if match:
            return urljoin(_BASE, unescape(match.group(1)))
    return ""


class LgMxAdapter(SiteAdapter):
    code = "lg_mx"
    name = "LG（墨西哥）"
    base_url = _BASE
    country = "墨西哥"
    channel = "LG 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "body"
    spec_entry_extra_wait_ms = 5000
    spec_entry_scroll_passes = 3
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = _CARD_SELECTOR
    spec_entry_scroll_max_passes = 20
    spec_entry_scroll_stable_rounds = 2
    # 当前动态入口实际可配对纯电视口径为 53 个尺寸型号、49 个系列；
    # 页面正文的 87 Resultados 还包含音响/组合/未能映射型号的卡片，入口审计另行记录。
    spec_entry_expected_model_count = 53
    spec_entry_expected_series_count = 49
    # 规格内容已在产品页 DOM 中公开渲染；不要只等待旧版 table，避免把
    # 正常 200 页面误判为 no_anchor_element。
    spec_page_wait = "#pdp-specs-section, main"
    spec_page_extra_wait_ms = 3000
    spec_page_scroll_passes = 4

    def spec_entry_url(self) -> str:
        return ENTRY

    def series_entries(self, open_dom):
        """遍历 LG 结果页，只登记纯电视型号并记录站方结果口径。"""
        current = ENTRY
        visited: set[str] = set()
        model_url: dict[str, str] = {}
        pages = 0
        raw_card_count = 0
        non_tv_card_count = 0
        unclassified_card_count = 0
        page_card_counts: list[int] = []
        page_tv_counts: list[int] = []
        page_non_tv_counts: list[int] = []
        termination_reason = "entry_failed"
        reported_result_count = None

        while current and current not in visited and pages < ENTRY_MAX_PAGES:
            visited.add(current)
            with open_dom(current, self.spec_entry_wait) as (res, dom, html):
                if dom is None or not res.ok:
                    termination_reason = "entry_failed"
                    break
                pages += 1
                cards = dom.sub(_CARD_SELECTOR, limit=60)
                page_card_counts.append(len(cards))
                page_tv = 0
                page_non_tv = 0
                for card in cards:
                    raw_card_count += 1
                    card_text = " ".join((card.self_text() or "").split())
                    if _NON_TV_CARD_RE.search(card_text):
                        non_tv_card_count += 1
                        page_non_tv += 1
                        continue
                    model, url = _card_product(card)
                    if not model:
                        unclassified_card_count += 1
                        continue
                    page_tv += 1
                    if model not in model_url:
                        model_url[model] = url
                page_tv_counts.append(page_tv)
                page_non_tv_counts.append(page_non_tv)
                if reported_result_count is None:
                    reported_result_count = _reported_result_count(
                        html, dom.text("body")
                    )
                next_url = _next_page_url(dom, html)
            if not next_url:
                termination_reason = "no_next"
                break
            if next_url.rstrip("/") == current.rstrip("/"):
                termination_reason = "repeated_next"
                break
            current = next_url
        else:
            termination_reason = "max_pages" if pages >= ENTRY_MAX_PAGES else "visited"

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": ENTRY,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination_reason,
            "details": {
                "reported_result_count": reported_result_count,
                "pages": pages,
                "raw_card_count": raw_card_count,
                "non_tv_card_count": non_tv_card_count,
                "unclassified_card_count": unclassified_card_count,
                "page_card_counts": page_card_counts,
                "page_tv_counts": page_tv_counts,
                "page_non_tv_counts": page_non_tv_counts,
                "entry": ENTRY,
            },
        }
        return [(series, urls) for series, urls in series_map.items()]

    def model_from_url(self, url: str) -> str:
        base = _clean_url(url)
        match = _PROD_RE.search(base)
        return re.sub(r"[^A-Za-z0-9]", "", match.group(1)).upper() if match else url

    def classify_spec_empty(self, dom: Dom | None, series: str = "",
                             model: str = "", url: str = "") -> str:
        """区分规格区缺失与公开规格节点未解析。"""
        if dom is None:
            return "empty"
        if dom.count("#pdp-specs-section") == 0:
            return "no_spec_section"
        if dom.count(
                "#pdp-specs-section .c-compare-selling__spec-name") == 0:
            return "spec_section_no_rows"
        return "spec_rows_unparsed"

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        """解析 LG MX 产品页公开的完整规格区。"""
        from ...spec_parser import parse_en_kv_pairs

        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty

        eval_js = getattr(dom, "eval_js", None)
        if callable(eval_js):
            try:
                triples = eval_js(r"""() => {
                    const section = document.querySelector('#pdp-specs-section');
                    if (!section) return [];
                    const root = section.querySelector('.c-all-specs-area') || section;
                    const clean = value => (value || '').replace(/\s+/g, ' ').trim();
                    return Array.from(
                        root.querySelectorAll('.c-compare-selling__spec-name')
                    ).map(nameNode => {
                        const row = nameNode.closest('li') || nameNode.parentElement;
                        const valueNode = row && row.querySelector(
                            '.c-compare-selling__spec-desc'
                        );
                        const rawItem = clean(nameNode.textContent);
                        const splitAt = rawItem.indexOf(' - ');
                        const category = splitAt > 0 ? rawItem.slice(0, splitAt) : '';
                        const item = splitAt > 0 ? rawItem.slice(splitAt + 3) : rawItem;
                        const value = clean(valueNode && valueNode.textContent);
                        return [category, item, value];
                    }).filter(row => row[1] && row[2]);
                }""") or []
                sheet = parse_en_kv_pairs(
                    triples, self.code, self.name, series, model, url
                )
                if sheet.rows:
                    return sheet
            except Exception:
                pass

        # 兼容旧版/其它模板仍使用表格的产品页。
        tables = dom.sub(".spec-table table, section[class*='spec'] table", limit=1)
        if not tables:
            tables = dom.sub("table", limit=1)
        if tables:
            return parse_en_kv_table(
                tables[0], self.code, self.name, series, model, url
            )
        return empty
