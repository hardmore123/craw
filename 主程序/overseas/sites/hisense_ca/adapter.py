"""Hisense 加拿大官网 TV SPEC 适配器（多级流程）。

入口是 Home Entertainment 的 View All 页面。页面可能把产品链接按懒加载
追加到 DOM；本适配器由公共 BrowserFetcher 持续滚动到链接集合稳定，再按
型号前缀与产品卡片文本排除投影/激光影院等非电视产品。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ...fetchers import Dom
from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_SPEC_JS = r"""() => {
  const sec = document.querySelector('section.ui-specifications, .specifications-table');
  if (!sec) return [];
  const out = [];
  sec.querySelectorAll('dl').forEach(dl => {
    let cat = '';
    let prev = dl.previousElementSibling;
    while (prev) {
      const t = (prev.textContent || '').trim();
      if (/^H[2-4]$/i.test(prev.tagName) && t) { cat = t; break; }
      prev = prev.previousElementSibling;
    }
    const dts = dl.querySelectorAll('dt');
    const dds = dl.querySelectorAll('dd');
    for (let i = 0; i < dts.length; i++) {
      const k = (dts[i].textContent || '').trim();
      const v = (dds[i] ? dds[i].textContent : '').trim();
      if (k) out.push([cat, k, v]);
    }
  });
  return out;
}"""

_BASE = "https://www.hisense-canada.com"
VIEW_ALL = f"{_BASE}/en/home-entertainment/view-all/"
_PROD_RE = re.compile(r"/home-entertainment/view-all/([^/?#]+)", re.I)
_TV_MODEL_RE = re.compile(r"^\d{2,3}[A-Za-z]", re.I)
# 投影/激光影院不是电视，按产品链接描述排除。
_PROJECTOR_RE = re.compile(
    r"projector|laser-cinema|laser-mini|trichroma|\bcinema\b", re.I)

# 每个尺寸按钮（.property-swatch）带 data-product JSON，含精确 ProductNo
# 和 ProductLink（全部尺寸型号的权威来源），比页面默认只渲染一个代表尺寸
# 的产品链接更完整。这段脚本一次性收集所有 swatch 的产品条目。
_SWATCH_JS = r"""() => {
  const out = [];
  document.querySelectorAll('[data-product]').forEach(e => {
    const raw = e.getAttribute('data-product');
    if (!raw) return;
    try {
      const d = JSON.parse(raw);
      const no = (d.ProductNo || '').toString().trim();
      const link = (d.ProductLink || '').toString().trim();
      if (no) out.push({no, link, series_name: (d.ProductName || '').toString().trim()});
    } catch (err) {}
  });
  return out;
}"""


def _model_from_href(href: str) -> str:
    match = _PROD_RE.search(href or "")
    if not match:
        return ""
    return match.group(1).split("_", 1)[0].upper()


def _series_of(model: str) -> str:
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


class HisenseCaAdapter(SiteAdapter):
    code = "hisense_ca"
    name = "Hisense（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "Hisense 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a[href*='/home-entertainment/view-all/']"
    spec_entry_extra_wait_ms = 4000
    spec_page_wait = "section.ui-specifications, .specifications-table, table"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/home-entertainment/view-all/']"
    spec_entry_scroll_max_passes = 80
    # 官网 view-all 共 40 个系列（含投影/激光影院）；排除投影后电视为 33 个
    # 系列，展开全部尺寸约 140 个型号。期望值以电视口径记录，实际以审计为准。
    spec_entry_expected_series_count = 33
    spec_entry_expected_model_count = 140

    def spec_entry_url(self) -> str:
        return VIEW_ALL

    def series_entries(self, open_dom):
        """View All → 电视产品页。

        每个尺寸按钮（.property-swatch）的 data-product 提供该尺寸型号的
        精确 ProductNo/ProductLink，是所有尺寸型号的权威来源；页面默认只渲染
        每个系列一个代表尺寸链接，只读 anchor 会漏掉其余尺寸。这里优先用
        swatch 收集全部尺寸型号，拿不到再退回 anchor。投影/激光影院按产品
        链接描述排除。
        """
        model_url: dict[str, str] = {}
        audit_meta: dict = {}
        status = "entry_failed"
        try:
            with open_dom(VIEW_ALL, self.spec_entry_wait) as (res, dom, html):
                audit_meta = dict(getattr(res, "meta", {}) or {})
                if dom is None or not res.ok:
                    self.last_entry_audit = {
                        "requested_url": VIEW_ALL, "termination_reason": status,
                        "details": {"status": res.status, "meta": audit_meta},
                    }
                    return []
                # 1) 权威来源：swatch data-product（全部尺寸型号）。
                eval_js = getattr(dom, "eval_js", None)
                if callable(eval_js):
                    for item in eval_js(_SWATCH_JS) or []:
                        if not isinstance(item, dict):
                            continue
                        model = str(item.get("no") or "").strip().upper()
                        link = clean_url(_BASE, item.get("link") or "")
                        if not model or not _TV_MODEL_RE.match(model):
                            continue
                        if _PROJECTOR_RE.search(link):
                            continue
                        if link:
                            model_url.setdefault(model, link)
                # 2) 回退：静态 anchor（swatch 不可用时至少保留代表尺寸）。
                if not model_url:
                    records = anchor_records(dom, self.spec_entry_wait, html,
                                             limit=1200)
                    for record in records:
                        href = clean_url(_BASE, record.get("href", ""))
                        model = _model_from_href(href)
                        if not model or not _TV_MODEL_RE.match(model):
                            continue
                        if _PROJECTOR_RE.search(href) or clearly_non_tv(
                                {**record, "href": href}):
                            continue
                        model_url.setdefault(model, href)
                status = str(audit_meta.get("scroll_termination") or "stable")
        except Exception as exc:
            audit_meta["exception"] = f"{type(exc).__name__}: {exc}"

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": VIEW_ALL,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": status,
            "details": {
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
        return _model_from_href(url) or url

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
