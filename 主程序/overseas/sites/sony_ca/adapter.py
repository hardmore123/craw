"""Sony 加拿大官网 TV SPEC 适配器（多级流程）。

主入口使用 Sony.ca 的 All TVs 页面，electronics.sony.ca 旧入口保留为 fallback。
产品路径和型号 token 可能含连字符，解析时只归一化型号用于系列分组，来源 URL
保持原样；入口范围之外的 accessories/audio 链接会被过滤。
"""
from __future__ import annotations

import json
import re

from ...fetchers import Dom
from ...models import L3_STRONG, SpecSheet
from ...spec_parser import GENERIC_SPEC_JS, parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_BASE = "https://www.sony.ca"
_FALLBACK_BASE = "https://electronics.sony.ca"
TV_LIST = f"{_BASE}/en/tv-video/televisions/c/all-tvs"
_FALLBACK_LIST = f"{_FALLBACK_BASE}/tv-video/c/televisions"
_PROD_RE = re.compile(
    r"/tv-video/televisions/(?![^?#]*(?:accessor|audio|speaker|sound))"
    r"[^?#]*/p/([a-z0-9-]+)", re.I)
_SERIES_RE = re.compile(r"^[A-Za-z]+\d{2,3}(.+)$")

# Sony all-tvs 列表卡片下的尺寸按钮是 Angular span[role=radio]，不是链接。
# 官方 OCC 接口 products/search 返回 16 个系列代表型号，代表型号详情接口的
# baseOptions[].options[] 列出该系列全部尺寸变体（code）。这段脚本在页面
# 上下文串行 fetch：先 search 拿系列代表，再逐个取详情汇总所有尺寸型号。
_VARIANTS_JS = r"""async () => {
  const origin = 'https://www.sony.ca';
  const q = '%3Arelevance%3AsnaAllCategories%3Aall-tvs';
  const searchUrl = origin + '/api/occ/v2/snaca/products/search'
    + '?fields=FULL&query=' + q + '&pageSize=48&currentPage=0'
    + '&lang=en_CA&curr=CAD&prov=ON';
  const out = {status: 0, series: []};
  try {
    const r = await fetch(searchUrl, {headers: {accept: 'application/json'}});
    out.status = r.status;
    if (!r.ok) return out;
    const data = await r.json();
    const reps = (data.products || []).map(p => ({code: p.code, url: p.url}))
      .filter(p => p.code);
    for (const rep of reps) {
      const detailUrl = origin + '/api/occ/v2/snaca/products/'
        + encodeURIComponent(rep.code)
        + '?fields=FULL&lang=en_CA&curr=CAD&prov=ON';
      const models = [];
      try {
        const dr = await fetch(detailUrl, {headers: {accept: 'application/json'}});
        if (dr.ok) {
          const p = await dr.json();
          const push = c => { if (c) models.push(c); };
          (p.baseOptions || []).forEach(bo =>
            (bo.options || []).forEach(o => push(o.code)));
          (p.variantOptions || []).forEach(o => push(o.code));
        }
      } catch (e) {}
      const codes = Array.from(new Set(models.length ? models : [rep.code]));
      out.series.push({rep: rep.code, models: codes});
      await new Promise(res => setTimeout(res, 120));
    }
  } catch (e) { out.error = String(e); }
  return out;
}"""


def _url_for_code(code: str) -> str:
    return f"{_BASE}/en/tv-video/televisions/all-tvs/p/{(code or '').lower()}"


def _model_from_url(url: str) -> str:
    match = _PROD_RE.search(url or "")
    return re.sub(r"-", "", match.group(1)).upper() if match else ""


def _series_of(model: str) -> str:
    clean = re.sub(r"-", "", model or "")
    match = _SERIES_RE.match(clean)
    return (match.group(1) if match else clean).upper()


def _balanced_json_text(text: str, start: int) -> str:
    stack: list[str] = []
    closing = {"}": "{", "]": "["}
    for index in range(start, len(text)):
        char = text[index]
        if char in "[{":
            stack.append(char)
        elif char in closing:
            if not stack or stack[-1] != closing[char]:
                return ""
            stack.pop()
            if not stack:
                return text[start:index + 1]
    return ""


def _embedded_json_values(text: str, field: str) -> list | dict:
    markers = [(f'"{field}":', False), (f'\\"{field}\\":', True)]
    decoder = json.JSONDecoder()
    for marker, escaped in markers:
        offset = 0
        while True:
            position = text.find(marker, offset)
            if position < 0:
                break
            start = text.find("[" if field == "classifications" else "{",
                              position + len(marker))
            if start < 0:
                break
            try:
                if escaped:
                    raw = _balanced_json_text(text, start)
                    value = json.loads(json.loads('"' + raw + '"')) if raw else None
                else:
                    value, _ = decoder.raw_decode(text[start:])
                if isinstance(value, (list, dict)):
                    return value
            except (ValueError, json.JSONDecodeError):
                pass
            offset = position + len(marker)
    return [] if field == "classifications" else {}


def _classification_triples(html: str) -> list[list[str]]:
    classifications = _embedded_json_values(html, "classifications")
    if not isinstance(classifications, list):
        return []
    triples: list[list[str]] = []
    for group in classifications:
        if not isinstance(group, dict):
            continue
        category = str(group.get("name") or group.get("code") or "").strip()
        for feature in group.get("features") or []:
            if not isinstance(feature, dict):
                continue
            item = str(feature.get("name") or "").strip()
            if not item:
                continue
            values = []
            for entry in feature.get("featureValues") or []:
                if isinstance(entry, dict):
                    value = str(entry.get("value") or "").strip()
                    if value and value not in values:
                        values.append(value)
            triples.append([category, item, " / ".join(values)])
    return triples


class SonyCaAdapter(SiteAdapter):
    code = "sony_ca"
    name = "Sony（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "Sony 官网"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 7.0
    supports_spec = True
    spec_entry_wait = "a[href*='/p/']"
    spec_entry_extra_wait_ms = 6000
    spec_page_wait = ".product-specification, [class*='product-specification']"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/p/']"
    spec_entry_scroll_max_passes = 80
    spec_entry_expected_series_count = 16

    def spec_entry_url(self) -> str:
        return TV_LIST

    def series_entries(self, open_dom):
        model_url: dict[str, str] = {}
        audit_meta: dict = {}
        used_url = TV_LIST
        termination = "entry_failed"
        used_variants = False
        for entry_url in (TV_LIST, _FALLBACK_LIST):
            try:
                with open_dom(entry_url, self.spec_entry_wait) as (res, dom, html):
                    audit_meta = dict(getattr(res, "meta", {}) or {})
                    if dom is None or not res.ok:
                        continue
                    # 1) 主入口优先用 OCC 接口把每个系列的全部尺寸变体取全。
                    if entry_url == TV_LIST:
                        eval_js = getattr(dom, "eval_js", None)
                        if callable(eval_js):
                            result = eval_js(_VARIANTS_JS)
                            if isinstance(result, dict):
                                audit_meta["variants_status"] = result.get("status")
                                for grp in result.get("series") or []:
                                    if not isinstance(grp, dict):
                                        continue
                                    for code in grp.get("models") or []:
                                        code = re.sub(r"-", "", str(code or "")).upper()
                                        if not code:
                                            continue
                                        model_url.setdefault(code, _url_for_code(code))
                        if model_url:
                            used_variants = True
                            used_url = entry_url
                            termination = "occ_variants"
                            break
                    # 2) 回退：DOM anchor（接口不可用/旧入口，保留代表尺寸）。
                    for record in anchor_records(dom, self.spec_entry_wait, html, limit=1600):
                        href = clean_url(_BASE if entry_url == TV_LIST else _FALLBACK_BASE,
                                         record.get("href", ""))
                        model = _model_from_url(href)
                        if not model or clearly_non_tv({**record, "href": href}):
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
            "fallback_urls": [TV_LIST, _FALLBACK_LIST],
            "expected_series_count": self.spec_entry_expected_series_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination,
            "details": {
                "meta": audit_meta,
                "used_variants": used_variants,
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
        return _model_from_url(url) or url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        triples = _classification_triples(dom.html())
        if not triples:
            eval_js = getattr(dom, "eval_js", None)
            if callable(eval_js):
                triples = eval_js(GENERIC_SPEC_JS) or []
        if not triples:
            return empty
        return parse_en_kv_pairs(triples, self.code, self.name, series, model, url)
