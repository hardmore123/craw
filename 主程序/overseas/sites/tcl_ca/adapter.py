"""TCL 加拿大官网 TV SPEC 适配器（多级流程）。

TCL 首页的 Televisions/All TVs 菜单是当前系列入口；集合页是稳定 fallback。
入口还会包含手机、显示器和家电，因此采用入口范围 + URL/卡片文本负向过滤，
不再把 `_TV_HINT` 当成电视准入条件。
"""
from __future__ import annotations

import re

from ...fetchers import Dom
from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_BASE = "https://ca-en.tcl.com"
ROOT = f"{_BASE}/"
TV_COLLECTION = f"{_BASE}/collections/tv"
_HANDLE_MODEL_RE = re.compile(r"-(\d{2,3}[a-z][a-z0-9\-]*)$", re.I)
_SERIES_HANDLE_RE = re.compile(r"/products/(?:tcl-)?([a-z]+\d+[a-z]+)-\d{2,3}$", re.I)

# 主页 TELEVISIONS 菜单列出当前在售系列，每个系列卡片指向一个代表尺寸的
# 产品页；该产品页上带同系列其它尺寸的 /products/ 链接。这段脚本在页面
# 上下文里：从菜单收集系列代表产品页，再逐个 fetch 代表页 HTML，抽出同系列
# 的全部尺寸型号链接（Shopify 同源，可直接 fetch）。
_TV_SERIES_JS = r"""async () => {
  const origin = 'https://ca-en.tcl.com';
  const modelOf = h => {
    const path = h.split('?')[0].replace(/\/$/, '');
    // 常规：slug 末尾 <尺寸><系列>，如 ...-85rm9l。
    let m = /-(\d{2,3}[a-z][a-z0-9]*)(?:$|[/?#])/i.exec(path);
    if (m) return m[1].toUpperCase();
    // X11L 型：/products/tcl-<系列>-<尺寸>，组合为 <尺寸><系列>。
    m = /\/products\/tcl-([a-z]+\d+[a-z]*)-(\d{2,3})$/i.exec(path);
    if (m) return (m[2] + m[1]).toUpperCase();
    return '';
  };
  const seriesOf = m => m.replace(/^\d{2,3}/, '').toUpperCase();
  // 1) 菜单里标注 SERIES 的产品链接 = 当前在售系列代表。
  const repHandles = new Map();  // series -> rep product url
  document.querySelectorAll('a[href*="/products/"]').forEach(a => {
    const txt = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
    if (!/SERIES/i.test(txt)) return;
    const url = a.href.split('?')[0];
    const model = modelOf(url);
    if (!model) return;
    const s = seriesOf(model);
    if (!repHandles.has(s)) repHandles.set(s, url);
  });
  // 2) 逐个代表页 fetch，抽同系列全部尺寸链接。
  const out = {};
  for (const [series, repUrl] of repHandles) {
    const models = new Set();
    try {
      const r = await fetch(repUrl, {headers: {accept: 'text/html'}});
      if (r.ok) {
        const html = await r.text();
        const re = /\/products\/[a-z0-9\-]+/gi;
        let m;
        while ((m = re.exec(html)) !== null) {
          const mo = modelOf(origin + m[0]);
          if (mo && seriesOf(mo) === series) models.add(mo + '\u0001' + origin + m[0]);
        }
      }
    } catch (e) {}
    // 代表页至少纳入自身。
    const repModel = modelOf(repUrl);
    if (repModel) models.add(repModel + '\u0001' + repUrl);
    out[series] = Array.from(models);
    await new Promise(res => setTimeout(res, 100));
  }
  return out;
}"""

_SPEC_JS = r"""() => {
  const out = [];
  document.querySelectorAll(
    '.specifications_box, [class*="specifications_box"]'
  ).forEach((box, index) => {
    const heading = box.querySelector(
      '.specifications_one .h3, [class*="specifications_one"] .h3'
    );
    const rawCategory = (heading ? heading.textContent : '')
      .replace(/\s+/g, ' ').trim();
    const category = index === 0 ? 'Summary' : rawCategory;
    const root = box.querySelector(
      '.specifications_two, [class*="specifications_two"]'
    ) || box;
    const cells = root.querySelectorAll(
      '.specifications_time, [class*="specifications_time"]'
    );
    for (let i = 0; i + 1 < cells.length; i += 2) {
      const item = (cells[i].textContent || '').replace(/\s+/g, ' ').trim();
      const value = (cells[i + 1].textContent || '').replace(/\s+/g, ' ').trim();
      if (item) out.push([category, item, value]);
    }
  });
  return out;
}"""


_X11L_HANDLE_RE = re.compile(r"/products/tcl-([a-z]+\d+[a-z]*)-(\d{2,3})$", re.I)


def _model_from_path(path: str) -> str:
    clean = path.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    match = _HANDLE_MODEL_RE.search(clean)
    if match:
        return match.group(1).upper()
    # X11L 型：/products/tcl-<系列>-<尺寸> → <尺寸><系列>。
    match = _X11L_HANDLE_RE.search(clean)
    if match:
        return (match.group(2) + match.group(1)).upper()
    match = _SERIES_HANDLE_RE.search(clean)
    return match.group(1).upper() if match else ""


def _series_of(model: str) -> str:
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


class TclCaAdapter(SiteAdapter):
    code = "tcl_ca"
    name = "TCL（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "TCL 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a[href*='/products/']"
    spec_entry_extra_wait_ms = 4000
    spec_page_wait = ".specifications_box, [class*='specifications_box']"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/products/']"
    spec_entry_scroll_max_passes = 80
    spec_entry_expected_series_count = 16

    def spec_entry_url(self) -> str:
        return ROOT

    def series_entries(self, open_dom):
        model_url: dict[str, str] = {}
        audit_meta: dict = {}
        used_url = ROOT
        termination = "entry_failed"
        used_menu = False
        # 主页 TELEVISIONS 菜单是用户指定入口（当前在售系列 × 多尺寸）；
        # collection 页作为回退。
        for entry_url in (ROOT, TV_COLLECTION):
            try:
                with open_dom(entry_url, self.spec_entry_wait) as (res, dom, html):
                    audit_meta = dict(getattr(res, "meta", {}) or {})
                    if dom is None or not res.ok:
                        continue
                    # 1) 主页：用菜单系列代表 + 代表页同系列尺寸链接补全全尺寸。
                    if entry_url == ROOT:
                        eval_js = getattr(dom, "eval_js", None)
                        if callable(eval_js):
                            result = eval_js(_TV_SERIES_JS)
                            if isinstance(result, dict):
                                for _series, entries in result.items():
                                    for token in entries or []:
                                        model, _, link = str(token).partition("\u0001")
                                        model = model.strip().upper()
                                        href = clean_url(_BASE, link)
                                        if not model or not href:
                                            continue
                                        if clearly_non_tv({"href": href}):
                                            continue
                                        model_url.setdefault(model, href)
                        if model_url:
                            used_menu = True
                            used_url = entry_url
                            termination = "menu_series"
                            break
                    # 2) 回退：collection 页静态 anchor。
                    records = anchor_records(dom, self.spec_entry_wait, html, limit=1800)
                    for record in records:
                        href = clean_url(_BASE, record.get("href", ""))
                        if "/products/" not in href.lower():
                            continue
                        if clearly_non_tv({**record, "href": href}):
                            continue
                        model = _model_from_path(href)
                        if not model:
                            continue
                        model_url.setdefault(model, href)
                    if model_url:
                        used_url = entry_url
                        termination = str(audit_meta.get("scroll_termination") or "stable")
                        break
            except Exception as exc:
                audit_meta["exception"] = f"{type(exc).__name__}: {exc}"

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": used_url,
            "fallback_urls": [TV_COLLECTION, ROOT],
            "expected_series_count": self.spec_entry_expected_series_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination,
            "details": {
                "meta": audit_meta,
                "used_menu": used_menu,
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
        return _model_from_path(url) or url

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
