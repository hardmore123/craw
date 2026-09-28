"""Samsung 加拿大官网 TV SPEC 适配器（多级流程）。"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ...fetchers import Dom
from ...models import L3_STRONG, SpecSheet
from ...spec_identity import family_series_key
from ...spec_parser import parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_BASE = "https://www.samsung.com"
TV_LIST = f"{_BASE}/ca/tvs/all-tvs/"
_PRODUCT_RE = re.compile(r"/ca/tvs/([^/]+)/([^/?#]+)", re.I)
_SIZE_SLUG_RE = re.compile(
    r"^(?P<series>.+?)-\d{2,3}-inch-(?P<body>.+)-(?P<sku>[a-z0-9]+)$", re.I)
# pdpUrl slug 末尾即 modelCode，如 .../u7900h-55-inch-...-un55u790mhfxzc/
_SKU_TAIL_RE = re.compile(r"-([a-z0-9]{8,})$", re.I)

# Samsung all-tvs 是 finder 接口分页渲染，DOM 只出首屏一批。官方接口一次
# 返回全部 64 个 family（系列），每个 family 的 modelList 是各尺寸型号，含
# modelCode 与 pdpUrl（规格页路径）。type=04010000 限定电视。
_FINDER_JS = r"""async () => {
  const url = "https://searchapi.samsung.com/v6/front/b2c/product/finder/global"
    + "?type=04010000&siteCode=ca&start=1&num=500&sort=newest"
    + "&onlyFilterInfoYN=N&keySummaryYN=Y";
  try {
    const r = await fetch(url, {headers: {accept: 'application/json'}});
    if (!r.ok) return {status: r.status, families: []};
    const data = await r.json();
    const rd = (data.response && data.response.resultData) || {};
    const list = rd.productList || [];
    const families = [];
    list.forEach((f, idx) => {
      const series = (f.fmyEngName || f.fmyMarketingName || '').toString().trim();
      const familyId = (f.familyId || f.productGroupId || '').toString().trim();
      const models = [];
      (f.modelList || []).forEach(m => {
        const code = (m.modelCode || '').toString().trim();
        const pdp = (m.pdpUrl || m.originPdpUrl || '').toString().trim();
        if (code) models.push({code, pdp, display: (m.displayName || '').toString().trim()});
      });
      // familyId 唯一标识一个系列；官方口径为 64 个 family，名称可能重复，
      // 不能按名称合并，否则会把不同 family 并成一个系列。
      if (models.length) families.push({series, familyId: familyId || ('idx' + idx), models});
    });
    return {status: r.status, families};
  } catch (e) {
    return {status: -1, error: String(e), families: []};
  }
}"""

_SPEC_JS = r"""() => {
  const out = [];
  document.querySelectorAll('[class*="spec"] [class*="item"]').forEach(it => {
    const tit = it.querySelector('[class*="tit"],[class*="name"],[class*="label"],dt');
    const des = it.querySelector('[class*="desc"],[class*="value"],[class*="txt"],dd');
    if (tit) {
      const k = (tit.textContent || '').replace(/\s+/g, ' ').trim();
      const v = (des ? des.textContent : '').replace(/\s+/g, ' ').trim();
      if (k) out.push(['', k, v]);
    }
  });
  return out;
}"""


def _series_from_sku(sku: str) -> str:
    """接口未给系列名时的兜底：去掉品牌前缀与尺寸数字后的型号主干。"""
    s = (sku or "").upper()
    s = re.sub(r"^(UN|QN|MRN)", "", s)
    s = re.sub(r"^\d{2,3}", "", s)
    return s or (sku or "").upper()


def _sku_from_pdp(url: str) -> str:
    """从 pdpUrl slug 末尾提取 modelCode（如 ...-un55u790mhfxzc/ → 大写）。"""
    path = urlsplit((url or "").split("?")[0]).path.rstrip("/")
    tail = path.rsplit("/", 1)[-1] if path else ""
    match = _SKU_TAIL_RE.search(tail)
    return match.group(1).upper() if match else ""


def _parts(url: str) -> tuple[str, str] | None:
    match = _PRODUCT_RE.search(urlsplit((url or "").split("?")[0]).path)
    if not match:
        return None
    slug = match.group(2).rstrip("/")
    parsed = _SIZE_SLUG_RE.match(slug)
    if not parsed:
        return None
    sku = parsed.group("sku").upper()
    if len(sku) < 5 or not re.search(r"[A-Z]", sku) or not re.search(r"\d", sku):
        return None
    return parsed.group("series").upper(), sku


class SamsungCaAdapter(SiteAdapter):
    code = "samsung_ca"
    name = "Samsung（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "Samsung 官网"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 7.0
    supports_spec = True
    spec_entry_wait = "a[href*='/ca/tvs/']"
    spec_entry_extra_wait_ms = 6000
    spec_page_wait = "[class*='spec']"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/ca/tvs/']"
    spec_entry_scroll_max_passes = 100
    # 官方 finder 接口共 64 个 family（系列）、约 224 个尺寸型号。
    spec_entry_expected_series_count = 64
    spec_entry_expected_model_count = 224

    def spec_entry_url(self) -> str:
        return TV_LIST

    def series_entries(self, open_dom):
        # 按 familyId 分桶保留官方 64 个 family；每桶记展示系列名和 sku→url。
        buckets: dict[str, dict] = {}
        order: list[str] = []
        meta: dict = {}
        termination = "entry_failed"
        used_finder = False
        try:
            with open_dom(TV_LIST, self.spec_entry_wait) as (res, dom, html):
                meta = dict(getattr(res, "meta", {}) or {})
                if dom is not None and res.ok:
                    # 1) 权威来源：官方 finder 接口（全部 64 family × 各尺寸型号）。
                    eval_js = getattr(dom, "eval_js", None)
                    if callable(eval_js):
                        result = eval_js(_FINDER_JS)
                        if isinstance(result, dict):
                            meta["finder_status"] = result.get("status")
                            for fam in result.get("families") or []:
                                if not isinstance(fam, dict):
                                    continue
                                fid = str(fam.get("familyId") or "").strip()
                                series_name = str(fam.get("series") or "").strip().upper()
                                for m in fam.get("models") or []:
                                    if not isinstance(m, dict):
                                        continue
                                    sku = str(m.get("code") or "").strip().upper()
                                    pdp = str(m.get("pdp") or "").strip()
                                    if not sku or not pdp:
                                        continue
                                    url = clean_url(_BASE, pdp)
                                    if clearly_non_tv({"href": url,
                                                       "card": str(m.get("display") or "")}):
                                        continue
                                    key = fid or series_name or _series_from_sku(sku)
                                    if key not in buckets:
                                        buckets[key] = {
                                            "name": series_name or _series_from_sku(sku),
                                            "models": {}}
                                        order.append(key)
                                    buckets[key]["models"][sku] = url
                    if buckets:
                        used_finder = True
                        termination = "finder_api"
                    # 2) 回退：DOM anchor（接口不可用时至少保留首屏可见型号）。
                    if not buckets:
                        for record in anchor_records(dom, self.spec_entry_wait, html, limit=2400):
                            href = clean_url(_BASE, record.get("href", ""))
                            if clearly_non_tv({**record, "href": href}):
                                continue
                            parsed = _parts(href)
                            if not parsed:
                                continue
                            series, sku = parsed
                            if series not in buckets:
                                buckets[series] = {"name": series, "models": {}}
                                order.append(series)
                            buckets[series]["models"][sku] = href
                        termination = str(meta.get("scroll_termination") or "stable")
        except Exception as exc:
            meta["exception"] = f"{type(exc).__name__}: {exc}"

        self.last_entry_audit = {
            "requested_url": TV_LIST,
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(buckets),
            "discovered_model_count": sum(len(b["models"]) for b in buckets.values()),
            "termination_reason": termination,
            "details": {
                "meta": meta,
                "used_finder": used_finder,
                "series": [
                    {"series": buckets[key]["name"],
                     "family_id": key,
                     "models": [{"model": sku, "url": url}
                                for sku, url in buckets[key]["models"].items()]}
                    for key in order
                ],
            },
        }
        # finder 的 familyId 是真正的入口身份；保留在内部系列键中，避免
        # 同名但不同 family 的条目在 spec_series/spec_series_status 中互相覆盖。
        # 入口审计 details 仍使用原始官网系列名和 family_id，供 API 展示。
        return [
            (family_series_key(buckets[key]["name"], key) if used_finder
             else buckets[key]["name"],
             list(buckets[key]["models"].values()))
            for key in order
        ]

    def model_from_url(self, url: str) -> str:
        parsed = _parts(url)
        if parsed:
            return parsed[1]
        sku = _sku_from_pdp(url)
        return sku or url

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
