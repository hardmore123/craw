"""Philips 加拿大官网 TV SPEC 适配器（多级流程）。

主入口为 /c-m-so/tv/latest，旧 /c-m-so/televisions 作为 fallback。入口页可能
包含推荐/配件链接，使用产品 URL、卡片文本和入口范围进行电视过滤。
"""
from __future__ import annotations

import json
import re

from ...fetchers import Dom
from ...models import L2_MEDIUM, SpecSheet
from ...spec_parser import GENERIC_SPEC_JS, parse_en_kv_pairs
from .. import SiteAdapter
from ..ca_spec_common import anchor_records, clearly_non_tv, clean_url

_BASE = "https://www.philips.ca"
TV_LIST = f"{_BASE}/c-m-so/tv/latest"
_FALLBACK_LIST = f"{_BASE}/c-m-so/televisions"
_PROD_RE = re.compile(r"/c-p/([A-Za-z0-9]+)_[A-Za-z0-9]+/", re.I)

# Philips latest 列表是 Next.js 前端分页（同一 URL、无查询参数），页码是
# aria-label="Show page N of results" 的按钮，点击后替换当前页产品卡而非
# 追加。这段脚本在页面上下文里逐页点击并累积全部 /c-p/ 链接及卡片文本，
# 供电视过滤复用；解析不到分页按钮时退回当前页链接。
_PAGINATE_JS = r"""async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const collected = new Map();
  const harvest = () => {
    document.querySelectorAll("a[href*='/c-p/']").forEach(a => {
      const href = a.href || a.getAttribute('href') || '';
      if (!href) return;
      const card = a.closest('[class*="product"], [class*="card"], article, li') || a.parentElement;
      const text = e => (e && (e.innerText || e.textContent) || '').replace(/\s+/g, ' ').trim();
      if (!collected.has(href)) {
        collected.set(href, {href, text: text(a), card: text(card),
                             aria: a.getAttribute('aria-label') || '',
                             title: a.getAttribute('title') || ''});
      }
    });
  };
  // 用编号页按钮 aria-label="Show page N of results" 逐页点击，比“下一页”
  // 箭头更稳（页面上还有轮播用的 next 箭头，不能混用）。
  const pageButton = (n) => Array.from(
    document.querySelectorAll('button,[role="button"]')).find(b =>
      (b.getAttribute('aria-label') || '') === `Show page ${n} of results`) || null;
  const visitPage = async (n) => {
    const btn = pageButton(n);
    if (!btn) return false;
    const before = collected.size;
    try {
      btn.scrollIntoView({block: 'center'});
      btn.click();
    } catch (e) { return true; }
    // 分页是替换渲染：轮询等待本页出现新链接，最多约 8 秒；有新增后再多
    // 采两轮兜底（一页产品卡可能分批渲染）。
    for (let w = 0; w < 16; w++) {
      await sleep(500);
      harvest();
      if (collected.size > before) {
        await sleep(700); harvest();
        await sleep(500); harvest();
        break;
      }
    }
    return true;
  };
  // 先探测总页数（页码按钮 aria-label="Show page N of results"）。
  let maxPage = 1;
  Array.from(document.querySelectorAll('button,[role="button"]')).forEach(b => {
    const m = /^Show page (\d+) of results$/.exec(b.getAttribute('aria-label') || '');
    if (m) maxPage = Math.max(maxPage, parseInt(m[1], 10));
  });
  harvest();
  // 走两轮全部页码，消除单轮替换渲染时的时序抖动。
  for (let round = 0; round < 2; round++) {
    for (let n = 2; n <= Math.min(maxPage, 40); n++) {
      await visitPage(n);
    }
    // 回到第 1 页再采一次，保证第 1 页卡片在最终 DOM 中也被收录。
    await visitPage(1);
    harvest();
  }
  return Array.from(collected.values());
}"""


def _series_of(model: str) -> str:
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


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


def _embedded_specification(html: str) -> dict:
    markers = [("\"specification\":", False), ("\\\"specification\\\":", True)]
    decoder = json.JSONDecoder()
    for marker, escaped in markers:
        offset = 0
        while True:
            position = html.find(marker, offset)
            if position < 0:
                break
            start = html.find("{", position + len(marker))
            if start < 0:
                break
            try:
                if escaped:
                    raw = _balanced_json_text(html, start)
                    value = json.loads(json.loads('"' + raw + '"')) if raw else None
                else:
                    value, _ = decoder.raw_decode(html[start:])
                if isinstance(value, dict) and value.get("csChapter"):
                    return value
            except (ValueError, json.JSONDecodeError):
                pass
            offset = position + len(marker)
    return {}


def _specification_triples(html: str) -> list[list[str]]:
    specification = _embedded_specification(html)
    triples: list[list[str]] = []
    for chapter in specification.get("csChapter") or []:
        if not isinstance(chapter, dict):
            continue
        category = str(chapter.get("csChapterName") or
                       chapter.get("csChapterCode") or "").strip()
        for item_data in chapter.get("csItem") or []:
            if not isinstance(item_data, dict):
                continue
            item = str(item_data.get("csItemName") or "").strip()
            if not item:
                continue
            values = []
            for value_data in item_data.get("csValue") or []:
                if isinstance(value_data, dict):
                    value = str(value_data.get("csValueName") or "").strip()
                    if value and value not in values:
                        values.append(value)
            triples.append([category, item, " / ".join(values)])
    return triples


class PhilipsCaAdapter(SiteAdapter):
    code = "philips_ca"
    name = "Philips（加拿大）"
    base_url = _BASE
    country = "Canada"
    channel = "Philips 官网"
    protection = L2_MEDIUM
    requires_browser = True
    suggested_interval = 5.0
    supports_spec = True
    spec_entry_wait = "a[href*='/c-p/']"
    spec_entry_extra_wait_ms = 5000
    spec_page_wait = "h1, h2"
    spec_entry_scroll_until_stable = True
    spec_entry_scroll_growth_selector = "a[href*='/c-p/']"
    spec_entry_scroll_max_passes = 80
    # 官网 latest 列表标题为 TV (25)，即 25 个型号；这些型号归并为若干系列。
    spec_entry_expected_model_count = 25
    spec_entry_expected_series_count = 11

    def spec_entry_url(self) -> str:
        return TV_LIST

    def series_entries(self, open_dom):
        model_url: dict[str, str] = {}
        meta: dict = {}
        used_url = TV_LIST
        termination = "entry_failed"
        for entry_url in (TV_LIST, _FALLBACK_LIST):
            try:
                with open_dom(entry_url, self.spec_entry_wait) as (res, dom, html):
                    meta = dict(getattr(res, "meta", {}) or {})
                    if dom is None or not res.ok:
                        continue
                    # 先在页面上下文逐页点击分页按钮，累积全部产品卡；
                    # 拿不到（无 eval_js/脚本失败）时退回静态 anchor 抽取。
                    records: list[dict[str, str]] = []
                    eval_js = getattr(dom, "eval_js", None)
                    if callable(eval_js):
                        paged = eval_js(_PAGINATE_JS)
                        if isinstance(paged, list):
                            records = [item for item in paged if isinstance(item, dict)]
                    if not records:
                        records = anchor_records(dom, self.spec_entry_wait, html,
                                                 limit=1800)
                    for record in records:
                        href = clean_url(_BASE, record.get("href", ""))
                        match = _PROD_RE.search(href)
                        if not match or clearly_non_tv({**record, "href": href}):
                            continue
                        model_url.setdefault(match.group(1).upper(), href)
                    if model_url:
                        used_url = entry_url
                        termination = str(meta.get("scroll_termination") or "paginated")
                        break
            except Exception as exc:
                meta["exception"] = f"{type(exc).__name__}: {exc}"

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(_series_of(model), []).append(url)
        self.last_entry_audit = {
            "requested_url": used_url,
            "fallback_urls": [TV_LIST, _FALLBACK_LIST],
            "expected_series_count": self.spec_entry_expected_series_count,
            "expected_model_count": self.spec_entry_expected_model_count,
            "discovered_series_count": len(series_map),
            "discovered_model_count": len(model_url),
            "termination_reason": termination,
            "details": {
                "meta": meta,
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
        match = _PROD_RE.search(url or "")
        return match.group(1).upper() if match else url

    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        triples = _specification_triples(dom.html())
        if not triples:
            eval_js = getattr(dom, "eval_js", None)
            if callable(eval_js):
                triples = eval_js(GENERIC_SPEC_JS) or []
        if not triples:
            return empty
        return parse_en_kv_pairs(triples, self.code, self.name, series, model, url)
