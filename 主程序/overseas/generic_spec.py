"""配置驱动的通用 SPEC 适配器（阶段二 POC）。

目标：验证"配置驱动"可行——由一份 AdapterSpec（JSON）声明入口/发现/解析规则，
GenericSpecAdapter 读取并执行，产出与手写适配器一致的 series_entries /
parse_spec_model 结果，而不为每个站写死一份 Python。

设计约束：
    - 只读配置，不生成/执行任意代码；能力固定，坏配置最多抓不到。
    - 复用现有 ca_spec_common（链接抽取/非 TV 过滤/URL 清洗）、spec_parser
      （三元组→SpecSheet）、open_dom 契约，保证与手写路径同源、结果可比。
    - 站点特有、无法纯声明的动态行为（如 Philips 的分页 JS）以"片段库"按名
      引用（snippet_ref），而非硬塞进引擎主逻辑，保持引擎通用。

本引擎覆盖 discover.mode="dom_anchor"（单层）与 "dom_anchor_two_level"（总览→系列
→型号两级发现）+ 可选 JS 分页 + 内嵌 JSON / 通用规格
两种解析模式，足以复现 Philips CA。其它模式（xhr_api / scroll / paginate=query_param
等）作为后续扩展点预留，未实现时显式报错而非静默降级。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .fetchers import Dom
from .models import L2_MEDIUM, SpecSheet
from .sites import SiteAdapter
from .sites.ca_spec_common import anchor_records, clearly_non_tv, clean_url
from .spec_parser import GENERIC_SPEC_JS, parse_en_kv_pairs

PROTECTION_MAP = {
    "L2_MEDIUM": L2_MEDIUM,
}

# ── 站点特有 JS 片段库（按 snippet_ref 引用）─────────────────────────
# Philips latest 列表：Next.js 前端分页，逐页点击 aria 页码按钮累积 /c-p/ 链接。
# 与手写适配器 overseas/sites/philips_ca/adapter.py 的 _PAGINATE_JS 逐字一致。
_PHILIPS_PAGINATE_JS = r"""async () => {
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
  let maxPage = 1;
  Array.from(document.querySelectorAll('button,[role="button"]')).forEach(b => {
    const m = /^Show page (\d+) of results$/.exec(b.getAttribute('aria-label') || '');
    if (m) maxPage = Math.max(maxPage, parseInt(m[1], 10));
  });
  harvest();
  for (let round = 0; round < 2; round++) {
    for (let n = 2; n <= Math.min(maxPage, 40); n++) {
      await visitPage(n);
    }
    await visitPage(1);
    harvest();
  }
  return Array.from(collected.values());
}"""

# 通用编号分页片段：适用于"点击编号页码/下一页按钮，替换或追加渲染"的分页站。
# 比 philips_paginate 更通用——不写死产品链接选择器，收集页面全部 <a href>
# 累积（引擎侧再用 link_selector 过滤），支持多种 aria-label 页码写法与"下一页"按钮。
_NUMBERED_PAGES_GENERIC_JS = r"""async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const collected = new Map();
  const harvest = () => {
    document.querySelectorAll('a[href]').forEach(a => {
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
  // 页码按钮：aria-label 含 "page N"（Show page N / page N of ...）
  const pageBtn = (n) => Array.from(
    document.querySelectorAll('button,[role="button"],a')).find(b => {
      const al = (b.getAttribute('aria-label') || '');
      // 注意转义层级：本片段是 Python raw string，此处 '\\bpage\\s+' 在 JS 里
      // 求值为 '\bpage\s+'，RegExp 才得到正确的 \b 单词边界（写 4 个反斜杠会
      // 变成匹配字面反斜杠，导致页码按钮永远匹配不到、翻页全失败）。
      return new RegExp('\\bpage\\s+' + n + '\\b', 'i').test(al);
    }) || null;
  const nextBtn = () => Array.from(
    document.querySelectorAll('button,[role="button"],a')).find(b => {
      const al = (b.getAttribute('aria-label') || '');
      return /\bnext\s+page\b/i.test(al) || /^next$/i.test(al.trim());
    }) || null;
  const clickAndWait = async (btn) => {
    if (!btn) return false;
    const before = collected.size;
    try { btn.scrollIntoView({block: 'center'}); btn.click(); } catch (e) { return false; }
    for (let w = 0; w < 16; w++) {
      await sleep(500); harvest();
      // 命中增长后再多等两段收割：替换式(Next.js)渲染慢，一次 harvest 常抓到旧内容
      if (collected.size > before) {
        await sleep(700); harvest();
        await sleep(500); harvest();
        break;
      }
    }
    return true;
  };
  // 扫描当前 DOM 里所有编号页码（替换式分页首屏常只渲染前几个页码，更大页码要翻页后
  // 才出现，因此每翻一页都要重扫，不能只在首屏探测一次）。
  const scanPages = () => {
    const pages = new Set();
    Array.from(document.querySelectorAll('button,[role="button"],a')).forEach(b => {
      const m = /\bpage\s+(\d+)\b/i.exec(b.getAttribute('aria-label') || '');
      if (m) pages.add(parseInt(m[1], 10));
    });
    return pages;
  };
  harvest();
  const known = scanPages();
  if (known.size > 0) {
    // 编号页码：动态重探测 + 翻到尽头。维护 visited 与待访问队列，每翻一页后重扫页码，
    // 把新出现的页码补进队列，直到无未访问页码且连续两轮 collected 不再增长
    // （对替换式渲染与追加式渲染都稳）。
    const visited = new Set([1]);
    let guard = 0;                       // 总翻页次数安全阀
    let stagnation = 0;                  // 连续无增长轮数
    while (guard < 80 && stagnation < 2) {
      const pending = Array.from(scanPages())
        .filter(n => !visited.has(n) && n <= 200)
        .sort((a, b) => a - b);
      if (pending.length === 0) break;
      const before = collected.size;
      for (const n of pending) {
        if (guard++ >= 80) break;
        visited.add(n);
        await clickAndWait(pageBtn(n));  // clickAndWait 内部已 harvest
      }
      stagnation = (collected.size > before) ? 0 : stagnation + 1;
    }
    await clickAndWait(pageBtn(1)); harvest();   // 回第 1 页补收
  } else {
    // 无编号页码则反复点"下一页"直到不再增长（最多 60 次安全阀）
    let prev = -1;
    for (let i = 0; i < 60; i++) {
      if (collected.size === prev) break;
      prev = collected.size;
      const ok = await clickAndWait(nextBtn());
      if (!ok) break;
    }
  }
  return Array.from(collected.values());
}"""

_SNIPPETS: dict[str, str] = {
    "philips_paginate": _PHILIPS_PAGINATE_JS,
    "numbered_pages_generic": _NUMBERED_PAGES_GENERIC_JS,
}


# ── 系列推导规则 ─────────────────────────────────────────────────
def _series_strip_leading_size(model: str) -> str:
    """去掉型号前导尺寸数字得到系列码（Philips/LG 等常见规则）。"""
    return re.sub(r"^\d{2,3}", "", model or "").upper() or (model or "").upper()


_SERIES_RULES = {
    "strip_leading_size": _series_strip_leading_size,
}


def _json_path(payload, path: str):
    """按点分路径定位 JSON 中的值，如 'response.resultData.productList'。

    路径段全是对象键；命中不到返回 None。数组用整数下标段（如 'items.0.list'）。
    """
    cur = payload
    for seg in str(path or "").split("."):
        if seg == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(seg)
        elif isinstance(cur, list) and seg.lstrip("-").isdigit():
            idx = int(seg)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


# ── 内嵌 JSON 规格解析（配置驱动的 Philips specification 版本）──────
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


def _embedded_root(html: str, root_marker: str, require_field: str = "") -> dict:
    """在 HTML 中定位 "<root_marker>": { ... } 的 JSON 对象（含转义变体）。

    require_field 非空时，只接受包含该字段的对象——页面里可能有多处同名
    "<root_marker>" 键（如组件级的空 specification），必须命中真正含 chapters
    的那个才返回（与手写 Philips 解析器一致，否则会取到错对象导致抽取为空）。
    """
    markers = [(f'"{root_marker}":', False), (f'\\"{root_marker}\\":', True)]
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
                if isinstance(value, dict) and value and \
                        (not require_field or value.get(require_field)):
                    return value
            except (ValueError, json.JSONDecodeError):
                pass
            offset = position + len(marker)
    return {}


def _embedded_json_triples(html: str, cfg: dict) -> list[list[str]]:
    """按 AdapterSpec.spec.embedded_json 的字段名从内嵌 JSON 抽三元组。"""
    chapters_field = str(cfg.get("chapters_field") or "csChapter")
    root = _embedded_root(html, str(cfg.get("root_marker") or "specification"),
                          require_field=chapters_field)
    if not root.get(chapters_field):
        return []
    chapter_name = str(cfg.get("chapter_name_field") or "csChapterName")
    chapter_code = str(cfg.get("chapter_code_field") or "csChapterCode")
    items_field = str(cfg.get("items_field") or "csItem")
    item_name = str(cfg.get("item_name_field") or "csItemName")
    values_field = str(cfg.get("values_field") or "csValue")
    value_name = str(cfg.get("value_name_field") or "csValueName")
    value_join = str(cfg.get("value_join") or " / ")

    triples: list[list[str]] = []
    for chapter in root.get(chapters_field) or []:
        if not isinstance(chapter, dict):
            continue
        category = str(chapter.get(chapter_name) or chapter.get(chapter_code) or "").strip()
        for item_data in chapter.get(items_field) or []:
            if not isinstance(item_data, dict):
                continue
            item = str(item_data.get(item_name) or "").strip()
            if not item:
                continue
            values: list[str] = []
            for value_data in item_data.get(values_field) or []:
                if isinstance(value_data, dict):
                    value = str(value_data.get(value_name) or "").strip()
                    if value and value not in values:
                        values.append(value)
            triples.append([category, item, value_join.join(values)])
    return triples


class GenericSpecAdapter(SiteAdapter):
    """由 AdapterSpec 驱动的通用 SPEC 适配器。

    通过工厂 build_generic_adapter(spec) 生成子类实例并注入 code/name 等类属性，
    以兼容 SiteRegistry 的按 code 注册与场景层的属性读取。
    """

    supports_spec = True

    # 由工厂填充
    spec_config: dict = {}

    def spec_entry_url(self) -> str:
        return str(self.spec_config.get("entry_url") or "")

    # ---- 发现 ----
    def series_entries(self, open_dom):
        """按 discover.mode 分派到单层 / 两级发现，统一写入口审计。"""
        cfg = self.spec_config
        discover = cfg.get("discover") or {}
        mode = str(discover.get("mode") or "dom_anchor")

        if mode == "dom_anchor":
            series_map, meta, used_url, termination = self._discover_single_level(
                open_dom, discover)
        elif mode == "dom_anchor_two_level":
            series_map, meta, used_url, termination = self._discover_two_level(
                open_dom, discover)
        elif mode == "xhr_api":
            series_map, meta, used_url, termination = self._discover_xhr_api(
                open_dom, discover)
            # 接口不可用时按配置回退 DOM 单层（api.fallback_dom=true 且给了 link_selector）
            if not series_map and discover.get("fallback_dom") and \
                    discover.get("link_selector"):
                fb_map, fb_meta, fb_url, fb_term = self._discover_single_level(
                    open_dom, discover)
                if fb_map:
                    meta = {**meta, "xhr_fallback": "dom_anchor", **fb_meta}
                    series_map, used_url, termination = fb_map, fb_url, \
                        f"xhr_fallback_{fb_term}"
        else:
            raise NotImplementedError(
                f"{self.code}: discover.mode={mode} 暂未在 POC 引擎实现")

        model_count = sum(len(urls) for urls in series_map.values())
        expected = cfg.get("expected") or {}
        self.last_entry_audit = {
            "requested_url": used_url,
            "expected_series_count": expected.get("series_count"),
            "expected_model_count": expected.get("model_count"),
            "discovered_series_count": len(series_map),
            "discovered_model_count": model_count,
            "termination_reason": termination,
            "details": {
                "meta": meta,
                "mode": mode,
                "generated_by": cfg.get("generated_by") or "generic_spec",
                "series": [
                    {"series": series,
                     "models": [{"model": self.model_from_url(url), "url": url}
                                for url in urls]}
                    for series, urls in series_map.items()
                ],
            },
        }
        return [(series, urls) for series, urls in series_map.items()]

    def _discover_single_level(self, open_dom, discover):
        """单层：入口页直接取型号链接（原 dom_anchor 逻辑）。

        返回 (series_map, meta, used_url, termination)。
        """
        cfg = self.spec_config
        base = str(cfg.get("base_url") or "")
        entry_urls = [str(cfg.get("entry_url") or "")]
        entry_urls += [str(u) for u in (cfg.get("fallback_entry_urls") or [])]
        entry_urls = [u for u in entry_urls if u]

        link_selector = str(discover.get("link_selector") or self.spec_entry_wait)
        model_regex = re.compile(str(discover.get("model_url_regex") or ""), re.I)
        series_rule = _SERIES_RULES.get(str(discover.get("series_rule") or ""),
                                        _series_strip_leading_size)
        non_tv = bool(discover.get("non_tv_filter"))
        paginate = discover.get("paginate") or {}
        ptype = str(paginate.get("type") or "")
        snippet = ""
        if ptype == "js_snippet":
            snippet = _SNIPPETS.get(str(paginate.get("snippet_ref") or ""), "")

        model_url: dict[str, str] = {}
        meta: dict = {}
        used_url = entry_urls[0] if entry_urls else ""
        termination = "entry_failed"

        def _harvest(records):
            """从一页 records 提型号并累积到 model_url，返回本页新增数。"""
            added = 0
            for record in records:
                href = clean_url(base, record.get("href", ""))
                match = model_regex.search(href) if model_regex.pattern else None
                if not match:
                    continue
                if non_tv and clearly_non_tv({**record, "href": href}):
                    continue
                key = match.group(1).upper()
                if key not in model_url:
                    model_url[key] = href
                    added += 1
            return added

        # query_param 分页：对入口 URL 逐页追加 ?<param>=<offset>，累积到无新增即停。
        if ptype == "query_param":
            series_map, meta, used_url, termination = self._discover_query_param(
                open_dom, discover, entry_urls, link_selector, base, _harvest,
                model_url, series_rule)
            return series_map, meta, used_url, termination

        for entry_url in entry_urls:
            try:
                with open_dom(entry_url, self.spec_entry_wait) as (res, dom, html):
                    meta = dict(getattr(res, "meta", {}) or {})
                    if dom is None or not res.ok:
                        continue
                    records: list[dict[str, str]] = []
                    eval_js = getattr(dom, "eval_js", None)
                    if snippet and callable(eval_js):
                        paged = eval_js(snippet)
                        if isinstance(paged, list):
                            records = [item for item in paged if isinstance(item, dict)]
                    if not records:
                        records = anchor_records(dom, link_selector, html, limit=1800)
                    _harvest(records)
                    if model_url:
                        used_url = entry_url
                        termination = str(meta.get("scroll_termination") or "paginated")
                        break
            except Exception as exc:
                meta["exception"] = f"{type(exc).__name__}: {exc}"

        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(series_rule(model), []).append(url)
        return series_map, meta, used_url, termination

    def _discover_query_param(self, open_dom, discover, entry_urls, link_selector,
                              base, harvest, model_url, series_rule):
        """query_param 分页：对入口 URL 逐页追加 ?<param>=<offset> 累积型号。

        配置 discover.paginate：
            type   = "query_param"
            param  ── 分页参数名（如 firstResult）
            step   ── 每页步长（默认 30）
            max_pages ── 安全阀（默认 50），防死循环
            stop_rounds ── 连续 N 页无新增即停（默认 2）
        offset 从 0 开始，按 step 递增；每页发现累积去重，连续无增长即停。
        """
        from urllib.parse import urlsplit, urlunsplit

        paginate = discover.get("paginate") or {}
        param = str(paginate.get("param") or "")
        step = int(paginate.get("step") or 30)
        max_pages = int(paginate.get("max_pages") or 50)
        stop_rounds = int(paginate.get("stop_rounds") or 2)
        entry_url = entry_urls[0] if entry_urls else ""

        meta: dict = {"paginate": "query_param", "param": param, "step": step}
        if not param or not entry_url:
            meta["error"] = "query_param 分页缺 param 或 entry_url"
            return {}, meta, entry_url, "config_incomplete"

        def _page_url(offset: int) -> str:
            parts = urlsplit(entry_url)
            # 保留已有查询串，覆盖/追加分页参数
            qs = [kv for kv in parts.query.split("&") if kv and
                  not kv.lower().startswith(param.lower() + "=")]
            qs.append(f"{param}={offset}")
            return urlunsplit((parts.scheme, parts.netloc, parts.path,
                               "&".join(qs), ""))

        no_growth = 0
        pages_fetched = 0
        termination = "max_pages"
        for i in range(max_pages):
            offset = i * step
            url = _page_url(offset)
            try:
                with open_dom(url, self.spec_entry_wait) as (res, dom, html):
                    pages_fetched += 1
                    if dom is None or not getattr(res, "ok", False):
                        no_growth += 1
                        if no_growth >= stop_rounds:
                            termination = "page_failed"
                            break
                        continue
                    records = anchor_records(dom, link_selector, html, limit=1800)
                    added = harvest(records)
                    if added == 0:
                        no_growth += 1
                        if no_growth >= stop_rounds:
                            termination = "no_growth"
                            break
                    else:
                        no_growth = 0
            except Exception as exc:
                meta.setdefault("exceptions", []).append(
                    f"offset={offset}: {type(exc).__name__}: {exc}")
                no_growth += 1
                if no_growth >= stop_rounds:
                    termination = "page_exception"
                    break

        meta["pages_fetched"] = pages_fetched
        series_map: dict[str, list[str]] = {}
        for model, url in model_url.items():
            series_map.setdefault(series_rule(model), []).append(url)
        return series_map, meta, entry_url, termination

    def _discover_two_level(self, open_dom, discover):
        """两级：入口页取系列页 URL → 逐系列页取型号 SPEC URL（松下/夏普型）。

        配置字段：
            series_link_selector / series_url_regex   ── 入口页 → 系列页
            model_link_selector  / model_url_regex    ── 系列页 → 型号页
        series_url_regex 的捕获组 1 作系列名（回落到 series_rule）。
        返回 (series_map, meta, used_url, termination)。
        """
        cfg = self.spec_config
        base = str(cfg.get("base_url") or "")
        entry_url = str(cfg.get("entry_url") or "")

        series_link_selector = str(discover.get("series_link_selector") or "a[href]")
        series_regex = re.compile(str(discover.get("series_url_regex") or ""), re.I)
        model_link_selector = str(discover.get("model_link_selector") or "a[href]")
        model_regex = re.compile(str(discover.get("model_url_regex") or ""), re.I)
        series_rule = _SERIES_RULES.get(str(discover.get("series_rule") or ""),
                                        _series_strip_leading_size)
        non_tv = bool(discover.get("non_tv_filter"))
        series_wait = str(discover.get("series_page_wait")
                          or model_link_selector or "body")

        meta: dict = {}
        termination = "entry_failed"

        # 第 1 级：入口页取系列页 URL（去重，保持发现顺序）
        series_pages: list[tuple[str, str]] = []
        seen_series: set[str] = set()
        try:
            with open_dom(entry_url, self.spec_entry_wait) as (res, dom, html):
                meta = dict(getattr(res, "meta", {}) or {})
                if dom is not None and res.ok:
                    for record in anchor_records(dom, series_link_selector, html,
                                                 limit=1800):
                        href = clean_url(base, record.get("href", ""))
                        match = series_regex.search(href) if series_regex.pattern else None
                        if not match:
                            continue
                        if non_tv and clearly_non_tv({**record, "href": href}):
                            continue
                        series_name = match.group(1).upper()
                        if series_name in seen_series:
                            continue
                        seen_series.add(series_name)
                        series_pages.append((series_name, href))
        except Exception as exc:
            meta["exception_entry"] = f"{type(exc).__name__}: {exc}"

        if not series_pages:
            return {}, meta, entry_url, termination

        # 第 2 级：逐系列页取型号 SPEC URL
        series_map: dict[str, list[str]] = {}
        for series_name, series_url in series_pages:
            model_urls: list[str] = []
            seen_models: set[str] = set()
            try:
                with open_dom(series_url, series_wait) as (res2, dom2, html2):
                    if dom2 is None or not res2.ok:
                        continue
                    for record in anchor_records(dom2, model_link_selector, html2,
                                                 limit=800):
                        href = clean_url(base, record.get("href", ""))
                        match = model_regex.search(href) if model_regex.pattern else None
                        if not match:
                            continue
                        if non_tv and clearly_non_tv({**record, "href": href}):
                            continue
                        model = match.group(1).upper()
                        if model in seen_models:
                            continue
                        seen_models.add(model)
                        model_urls.append(href)
            except Exception as exc:
                meta.setdefault("exception_series", {})[series_name] = \
                    f"{type(exc).__name__}: {exc}"
            if model_urls:
                # 系列名优先用 series_url_regex 的捕获；语义上仍按 series_rule 归并，
                # 避免同系列不同尺寸被拆开（与单层一致）。
                key = series_rule(series_name) if str(discover.get("series_from")
                                                       or "") == "model" else series_name
                series_map.setdefault(key, []).extend(model_urls)

        termination = "two_level_ok" if series_map else "no_models"
        return series_map, meta, entry_url, termination

    def _discover_xhr_api(self, open_dom, discover):
        """接口发现：请求官方 JSON 接口，按字段映射提型号（Samsung finder 型）。

        配置 discover.api：
            url_template     ── 接口 URL（可含 num=500 之类全量参数）
            list_path        ── JSON 中产品列表的点分路径，如
                                 "response.resultData.productList"
            model_field      ── 型号字段名（列表项内）
            url_field        ── 型号详情页 URL 字段名（可选；缺省用 model_url_template）
            model_url_template ── 无 url_field 时按型号拼 URL，用 {model} 占位
            series_field     ── 系列名字段名（可选，缺省按 series_rule 从型号推）
            family_id_field  ── 系列身份键字段名（可选，优先于 series_field 作归并键，
                                 防同名系列覆盖，对应 identity.series_key=family_id）
        比 DOM 稳、能一次拿全量、型号/系列直接读字段（避免 slug 混乱）。
        返回 (series_map, meta, used_url, termination)。
        """
        cfg = self.spec_config
        base = str(cfg.get("base_url") or "")
        api = discover.get("api") or {}
        url_template = str(api.get("url_template") or "")
        list_path = str(api.get("list_path") or "")
        model_field = str(api.get("model_field") or "")
        url_field = str(api.get("url_field") or "")
        model_url_template = str(api.get("model_url_template") or "")
        series_field = str(api.get("series_field") or "")
        family_id_field = str(api.get("family_id_field") or "")
        series_rule = _SERIES_RULES.get(str(discover.get("series_rule") or ""),
                                        _series_strip_leading_size)

        meta: dict = {"api_url": url_template, "list_path": list_path}
        self._xhr_model_map = {}
        if not url_template or not list_path or not model_field:
            meta["error"] = "xhr_api 配置不完整（需 url_template/list_path/model_field）"
            return {}, meta, url_template, "config_incomplete"

        # 用 open_dom 打开接口 URL；JSON 接口无 DOM，取 res.html 当响应体。
        payload = None
        try:
            with open_dom(url_template, "") as (res, dom, html):
                meta["status"] = getattr(res, "status", 0)
                body = html or ""
                if not body:
                    meta["error"] = "接口响应为空"
                    return {}, meta, url_template, "api_empty"
                try:
                    payload = json.loads(body)
                except (ValueError, TypeError) as exc:
                    meta["error"] = f"接口响应非 JSON: {exc}"
                    return {}, meta, url_template, "api_not_json"
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
            return {}, meta, url_template, "api_failed"

        items = _json_path(payload, list_path)
        if not isinstance(items, list):
            meta["error"] = f"list_path 未定位到数组: {list_path}"
            return {}, meta, url_template, "list_path_miss"
        meta["item_count"] = len(items)

        series_map: dict[str, list[str]] = {}
        seen_models: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            model = str(item.get(model_field) or "").strip()
            if not model or model in seen_models:
                continue
            seen_models.add(model)
            # 详情页 URL：优先字段，否则按模板拼
            if url_field and item.get(url_field):
                murl = clean_url(base, str(item.get(url_field)))
            elif model_url_template:
                murl = clean_url(base, model_url_template.format(model=model))
            else:
                murl = clean_url(base, model)
            # 系列归并键：family_id 优先（防同名覆盖），否则 series_field，否则按型号推
            if family_id_field and item.get(family_id_field) is not None:
                key = str(item.get(family_id_field)).strip().upper()
            elif series_field and item.get(series_field):
                key = str(item.get(series_field)).strip().upper()
            else:
                key = series_rule(model.upper())
            series_map.setdefault(key, []).append(murl)
            # 记住 URL→型号，供 model_from_url 直接返回字段型号（不靠 URL 正则）
            self._xhr_model_map[murl] = model.upper()

        termination = "xhr_ok" if series_map else "no_items"
        return series_map, meta, url_template, termination

    def model_from_url(self, url: str) -> str:
        # xhr_api 模式：型号来自接口字段，优先查发现时建立的映射。
        # 缓存 key 是 clean_url 归一后的 URL，查询前同样归一，避免尾斜杠/查询串差异。
        cached = getattr(self, "_xhr_model_map", None)
        if cached:
            base = str(self.spec_config.get("base_url") or "")
            for candidate in (url, clean_url(base, url)):
                if candidate in cached:
                    return cached[candidate]
        discover = self.spec_config.get("discover") or {}
        model_regex = re.compile(str(discover.get("model_url_regex") or ""), re.I)
        match = model_regex.search(url or "") if model_regex.pattern else None
        return match.group(1).upper() if match else url

    # ---- 解析 ----
    def parse_spec_model(self, dom: Dom | None, series: str, model: str,
                         url: str = "") -> SpecSheet:
        empty = SpecSheet(brand=self.code, brand_name=self.name, series=series,
                          url=url, models=[model])
        if dom is None:
            return empty
        spec_cfg = self.spec_config.get("spec") or {}
        order = spec_cfg.get("extract_order") or ["generic_spec_js"]
        triples: list[list[str]] = []
        for method in order:
            if method == "embedded_json_philips" or method == "embedded_json":
                triples = _embedded_json_triples(
                    dom.html(), spec_cfg.get("embedded_json") or {})
            elif method == "generic_spec_js":
                eval_js = getattr(dom, "eval_js", None)
                if callable(eval_js):
                    triples = eval_js(GENERIC_SPEC_JS) or []
            else:
                continue
            if triples:
                break
        if not triples:
            return empty
        return parse_en_kv_pairs(triples, self.code, self.name, series, model, url)


def load_spec(path: str | Path) -> dict:
    """读取一份 AdapterSpec JSON。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_generic_adapter(spec: dict) -> GenericSpecAdapter:
    """由 AdapterSpec 生成一个已注入类属性的 GenericSpecAdapter 实例。"""
    fetch = spec.get("fetch") or {}
    attrs: dict[str, Any] = {
        "code": str(spec.get("code") or ""),
        "name": str(spec.get("brand_name") or spec.get("code") or ""),
        "base_url": str(spec.get("base_url") or ""),
        "country": str(spec.get("country") or ""),
        "channel": str(spec.get("channel") or ""),
        "protection": PROTECTION_MAP.get(str(fetch.get("protection") or ""), L2_MEDIUM),
        "requires_browser": bool(fetch.get("requires_browser", True)),
        "suggested_interval": float(fetch.get("interval_sec") or 6.0),
        "supports_spec": True,
        "spec_entry_wait": str(fetch.get("entry_wait") or "body"),
        "spec_entry_extra_wait_ms": int(fetch.get("entry_extra_wait_ms") or 0),
        "spec_page_wait": str(fetch.get("page_wait") or "table"),
        "spec_entry_scroll_until_stable": bool(fetch.get("scroll_until_stable")),
        "spec_entry_scroll_growth_selector": str(fetch.get("scroll_growth_selector") or ""),
        "spec_entry_scroll_max_passes": int(fetch.get("scroll_max_passes") or 80),
        "spec_config": spec,
    }
    cls = type(f"GenericSpecAdapter_{attrs['code']}", (GenericSpecAdapter,), attrs)
    return cls()
