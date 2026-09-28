"""LG 加拿大官网 TV SPEC 适配器（多级流程）。

优先遍历用户给出的 all-tv-soundbars 页面，并按 firstResult 分页参数持续请求，
页面内 View more 由公共浏览器点击循环展开；旧的面板类别页作为 fallback。产品
入口位于混合的 TV/Soundbars 路径下，所以 URL、卡片文本和型号形态共同过滤。
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit, urlunsplit

from ...fetchers import Dom
from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_BASE = "https://www.lg.com"
_ALL_TV = "/ca_en/tv-soundbars/all-tv-soundbars/"
_CATEGORY_PAGES = [
    "/ca_en/tv-soundbars/oled-evo/",
    "/ca_en/tv-soundbars/oled/",
    "/ca_en/tv-soundbars/micro-rgb-evo/",
    "/ca_en/tv-soundbars/qned-evo-mini-led/",
    "/ca_en/tv-soundbars/qned-mini-led/",
    "/ca_en/tv-soundbars/qned/",
    "/ca_en/tv-soundbars/nano-4k-uhd/",
    "/ca_en/tv-soundbars/gallery-tv-with-frame/",
    "/ca_en/tv-soundbars/ultra-big-tvs/",
]
# 产品页型号 slug 既有 OLED 前缀（如 oled77b6eua），也有纯尺寸前缀
#（如 85qned75bua）；类别页的 slug 含连字符或不带尺寸，不会匹配。
_PROD_RE = re.compile(
    r"/ca_en/tv-soundbars/[a-z0-9\-]+/((?:oled)?\d{2,3}[a-z0-9]{4,})/?$",
    re.I)
_MODEL_RE = re.compile(
    r"^(?P<prefix>oled)?(?P<size>\d{2,3})(?P<tail>[a-z0-9]{4,})$", re.I)
_FIRST_RESULT_RE = re.compile(r"[?&]firstResult=(\d+)", re.I)

_SPEC_JS = r"""() => {
  const out = [];
  const tables = document.querySelectorAll(
    '.c-compare-selling__table, [class*="compare-selling__table"]'
  );
  tables.forEach(table => {
    const head = table.querySelector(
      '.c-compare-selling__table-head, [class*="table-head"]'
    );
    const category = (head ? head.textContent : '').replace(/\s+/g, ' ').trim();
    const names = table.querySelectorAll(
      '.c-compare-selling__spec-name, [class*="spec-name"]'
    );
    const descs = table.querySelectorAll(
      '.c-compare-selling__spec-desc, [class*="spec-desc"]'
    );
    for (let i = 0; i < names.length; i++) {
      const item = (names[i].textContent || '').replace(/\s+/g, ' ').trim();
      const value = (descs[i] ? descs[i].textContent : '')
        .replace(/\s+/g, ' ').trim();
      if (item) out.push([category, item, value]);
    }
  });
  return out;
}"""


# all-tv 列表卡片的尺寸 swatch 无 href，DOM 只渲染每个系列一个代表尺寸。
# 但每个产品详情页含同系列全部尺寸的 <a href>（如 B6E 页有 55/65/77）。
# 这段脚本在页面上下文里逐个 fetch 代表 URL 的详情页 HTML，抽同系列全部
# 尺寸型号路径（Shopify 之外的 LG 同源页面，可直接 fetch）。
_EXPAND_SIZES_JS = r"""async (repUrls) => {
  const origin = 'https://www.lg.com';
  const seriesOf = m => {
    const mm = /^(oled)?(\d{2,3})([a-z0-9]{4,})$/i.exec(m);
    return mm ? ((mm[1] || '') + mm[3]).toUpperCase() : m.toUpperCase();
  };
  // 捕获同系列尺寸型号的完整 path：/ca_en/tv-soundbars/<类别>/<型号>/
  const pathRe = /\/ca_en\/tv-soundbars\/([a-z0-9\-]+)\/((?:oled)?\d{2,3}[a-z0-9]{4,})\//gi;
  const catOf = u => {
    const mm = /\/ca_en\/tv-soundbars\/([a-z0-9\-]+)\//i.exec(u);
    return mm ? mm[1].toLowerCase() : '';
  };
  const modelTail = /\/((?:oled)?\d{2,3}[a-z0-9]{4,})\/$/i;
  const out = {};
  for (const repUrl of repUrls) {
    const repModelMatch = modelTail.exec(repUrl.replace(/\/?$/, '/'));
    const repModel = repModelMatch ? repModelMatch[1].toUpperCase() : '';
    const repSeries = repModel ? seriesOf(repModel) : '';
    const repCat = catOf(repUrl);
    try {
      const r = await fetch(repUrl, {headers: {accept: 'text/html'}});
      if (r.ok) {
        const html = await r.text();
        const seen = new Set();
        let m;
        while ((m = pathRe.exec(html)) !== null) {
          const cat = m[1].toLowerCase();
          const model = m[2].toUpperCase();
          // 只接受同系列且与代表同一类别路径的尺寸链接，避免抽到推荐区
          // 里跨类别的历史/错误链接（那些 URL 常 404）。
          if (repSeries && seriesOf(model) === repSeries && cat === repCat) {
            out[model] = origin + '/ca_en/tv-soundbars/' + cat + '/'
              + model.toLowerCase();
          }
        }
      }
    } catch (e) {}
    if (repModel) out[repModel] = out[repModel] || repUrl.replace(/\/$/, '');
    await new Promise(res => setTimeout(res, 120));
  }
  return out;
}"""


def _page_url(base: str, offset: int) -> str:
    parts = urlsplit(base)
    query = f"firstResult={offset}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _series_of(model: str) -> str:
    match = _MODEL_RE.fullmatch((model or "").strip())
    if match:
        prefix = (match.group("prefix") or "").upper()
        return f"{prefix}{match.group('tail').upper()}"
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


class LgCaAdapter(SiteAdapter):
    code = "lg_ca"
    name = "LG（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "LG 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a[href*='/tv-soundbars/']"
    spec_entry_extra_wait_ms = 6000
    spec_page_wait = ".c-compare-selling__spec-name, [class*='spec-name']"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/tv-soundbars/']"
    spec_entry_scroll_max_passes = 60
    spec_entry_scroll_stable_rounds = 2
    # 加拿大 All TVs 当前通过 firstResult 分页，不使用 View more；避免把
    # 页内通用按钮误当成入口展开控件而重复点击。
    spec_entry_click_selectors: list[str] = []
    spec_entry_click_repeats = 0
    spec_entry_click_wait_ms = 0
    spec_entry_click_growth_selector = ""
    # 官网 35 个系列，每系列多尺寸；型号总数以入口审计的展开结果为准。
    spec_entry_expected_series_count = 35
    spec_entry_expected_model_count = None

    def spec_entry_url(self) -> str:
        return _BASE + _ALL_TV

    def _collect_page(self, open_dom, url: str, model_url: dict[str, str]) -> tuple[int, set[int], dict]:
        page_models: set[str] = set()
        offsets: set[int] = set()
        meta: dict = {}
        with open_dom(url, self.spec_entry_wait) as (res, dom, html):
            meta = dict(getattr(res, "meta", {}) or {})
            if dom is None or not res.ok:
                return 0, offsets, meta
            records = anchor_records(dom, self.spec_entry_wait, html, limit=1800)
            for record in records:
                href = clean_url(_BASE, record.get("href", ""))
                match = _PROD_RE.search(urlsplit(href).path)
                # /tv-soundbars/ 是 LG 的产品栏目路径，不代表当前卡片是 Soundbar；
                # 非电视判断只看卡片文本、标题和 aria 等产品语义字段。
                if not match or clearly_non_tv({**record, "href": ""}):
                    continue
                model = match.group(1).upper()
                page_models.add(model)
                model_url.setdefault(model, href)
            # 优先沿单向 Next page 推进，避免 Previous/Page 链接在 LG
            # 动态结果重排后产生回退偏移和重复页面；没有 Next 时才使用
            # 数字页链接作为兼容兜底。
            next_offsets: set[int] = set()
            page_offsets: set[int] = set()
            for record in records:
                raw_href = str(record.get("href") or "")
                match = _FIRST_RESULT_RE.search(raw_href)
                if not match:
                    continue
                label = " ".join(str(record.get(key) or "")
                                 for key in ("text", "aria", "title"))
                value = int(match.group(1))
                if re.search(r"\bnext\b", label, re.I):
                    next_offsets.add(value)
                elif (re.search(r"\bpage\b", label, re.I)
                      or str(record.get("text") or "").strip().isdigit()):
                    page_offsets.add(value)
            offsets.update(next_offsets or page_offsets)
            return len(page_models), offsets, meta

    def series_entries(self, open_dom):
        model_url: dict[str, str] = {}
        audit_meta: dict = {}
        termination = "entry_failed"
        used_url = self.spec_entry_url()
        # 动态分页：优先跟随页面暴露的 firstResult；没有分页链接时按当前页
        # 唯一产品数推进，并在集合不再增长时停止。
        pending: list[tuple[str, int]] = [(_BASE + _ALL_TV, 0)]
        visited: set[int] = set()
        empty_rounds = 0
        while pending and len(visited) < 100:
            base, offset = pending.pop(0)
            if offset in visited:
                continue
            visited.add(offset)
            url = _page_url(base, offset) if offset else base
            try:
                before = len(model_url)
                page_count, discovered_offsets, audit_meta = self._collect_page(
                    open_dom, url, model_url)
                growth = len(model_url) - before
                if growth:
                    empty_rounds = 0
                    used_url = url
                    termination = str(audit_meta.get("scroll_termination") or "stable")
                else:
                    empty_rounds += 1
                for next_offset in sorted(discovered_offsets):
                    if next_offset not in visited:
                        pending.append((base, next_offset))
                if not discovered_offsets and page_count:
                    step = max(1, page_count)
                    next_offset = offset + step
                    if growth and next_offset not in visited:
                        pending.append((base, next_offset))
                if not growth and not pending:
                    termination = "page_set_stable"
                    break
            except Exception as exc:
                audit_meta["exception"] = f"{type(exc).__name__}: {exc}"
                if not pending:
                    termination = "entry_error"
        if len(visited) >= 100 and pending:
            termination = "pagination_safety_limit"

        # all-tv 页面被边缘防护拒绝时，回退旧类别页；仍使用同一稳定滚动/点击契约。
        if not model_url:
            for cat in _CATEGORY_PAGES:
                try:
                    self._collect_page(open_dom, _BASE + cat, model_url)
                except Exception:
                    continue

        # 尺寸展开：分页只拿到每个系列一个代表尺寸；逐个代表 fetch 详情页，
        # 抽同系列全部尺寸型号，补齐多尺寸。失败时保留原代表。
        expanded_count = 0
        if model_url:
            rep_urls = list(dict.fromkeys(model_url.values()))
            try:
                with open_dom(_BASE + _ALL_TV, self.spec_entry_wait) as (res, dom, _html):
                    eval_js = getattr(dom, "eval_js", None) if dom is not None else None
                    if callable(eval_js):
                        # PlaywrightDom.eval_js 只接受脚本文本，参数用 IIFE 传入。
                        result = eval_js(
                            "(" + _EXPAND_SIZES_JS + ")("
                            + json.dumps(rep_urls) + ")")
                        if isinstance(result, dict):
                            for model, url in result.items():
                                model = str(model or "").upper()
                                url = clean_url(_BASE, str(url or ""))
                                if model and url:
                                    if model not in model_url:
                                        expanded_count += 1
                                    model_url.setdefault(model, url)
            except Exception:
                pass

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": used_url,
            "fallback_urls": [_BASE + _ALL_TV] + [_BASE + x for x in _CATEGORY_PAGES],
            "expected_series_count": self.spec_entry_expected_series_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination,
            "details": {
                "visited_offsets": sorted(visited),
                "expanded_size_models": expanded_count,
                "meta": audit_meta,
                "series": [
                    {"series": series,
                     "models": [{"model": self.model_from_url(url), "url": url}
                                for url in urls]}
                    for series, urls in series_map.items()
                ],
            },
        }
        return [(series, urls) for series, urls in series_map.items()]

    def model_from_url(self, url: str) -> str:
        match = _PROD_RE.search(urlsplit((url or "").split("?")[0]).path)
        return match.group(1).upper() if match else url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        eval_js = getattr(dom, "eval_js", None)
        if not callable(eval_js):
            return empty
        triples = eval_js(_SPEC_JS)
        if not triples:
            return empty
        return parse_en_kv_pairs(triples, self.code, self.name, series, model, url)
