"""阶段三：由探测报告经 LLM 生成 AdapterSpec，并做 schema + 安全校验。

流程：probe_report(JSON) --prompt--> LLM --> AdapterSpec(JSON) --> 校验。

安全与稳定护栏（LLM 只产配置、不产代码，仍需防坏配置）：
    - 输出必须是能解析的 JSON，且含引擎认识的核心字段；
    - discover.mode 必须在引擎已实现的白名单内；
    - 所有正则（model_url_regex 等）必须可编译；
    - entry_url / api.url_template 必须与探测报告的入口同源或在白名单域名内（防 SSRF）；
    - paginate.snippet_ref 必须在片段库登记（不允许 LLM 凭空引用未知脚本）。

本模块只负责"生成 + 校验候选 Spec"，不执行抓取、不写数据库。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from .llm_client import LLMClient, LLMUnavailable

# 引擎当前已实现、允许 LLM 产出的发现模式（与 generic_spec.py 保持同步）。
SUPPORTED_DISCOVER_MODES = {"dom_anchor", "dom_anchor_two_level", "xhr_api"}
# 引擎登记的 JS 片段（与 generic_spec._SNIPPETS 的键保持同步）。
KNOWN_SNIPPETS = {"philips_paginate", "numbered_pages_generic"}
# 允许的解析方法（与 generic_spec.parse_spec_model 的分支同步）。
SUPPORTED_EXTRACT_METHODS = {"embedded_json", "embedded_json_philips", "generic_spec_js"}

_SYSTEM_PROMPT = (
    "你是网站抓取适配器的配置生成器。根据给定的\"探测报告\"，只输出一份严格符合 "
    "AdapterSpec schema 的 JSON 配置（不要输出任何解释、Markdown 代码块标记或多余文本）。"
    "你只能产出配置字段，绝不产出可执行代码。若某字段无法从报告确定，按 schema 默认值处理。"
)

_SCHEMA_HINT = """AdapterSpec 字段（JSON）：
{
  "spec_version": "1.0",
  "code": "<站点代号>",
  "brand_name": "<展示名>",
  "region": "<区域码>",
  "base_url": "<站点根，如 https://www.example.com>",
  "entry_url": "<电视总览入口 URL>",
  "fallback_entry_urls": ["<可选备用入口>"],
  "fetch": {
    "protection": "L2_MEDIUM",
    "requires_browser": true,
    "interval_sec": 5.0,
    "entry_wait": "<入口页等待的 CSS 选择器>",
    "entry_extra_wait_ms": 5000,
    "page_wait": "<型号页等待选择器>",
    "scroll_until_stable": true,
    "scroll_growth_selector": "<判断增长的选择器>",
    "scroll_max_passes": 80
  },
  "discover": {
    "mode": "dom_anchor",
    "link_selector": "<产品链接选择器>",
    "model_url_regex": "<从产品 URL 提取型号的正则，含1个捕获组>",
    "series_rule": "strip_leading_size",
    "non_tv_filter": true,
    "paginate": {"type": "js_snippet", "snippet_ref": "philips_paginate"}
  },
  "spec": {
    "extract_order": ["embedded_json", "generic_spec_js"],
    "embedded_json": {
      "root_marker": "specification",
      "chapters_field": "csChapter", "chapter_name_field": "csChapterName",
      "chapter_code_field": "csChapterCode", "items_field": "csItem",
      "item_name_field": "csItemName", "values_field": "csValue",
      "value_name_field": "csValueName", "value_join": " / "
    }
  },
  "identity": {"series_key": "series_name", "model_normalize": "upper_alnum"},
  "expected": {"series_count": null, "model_count": null}
}
约束：discover.mode 只能是 "dom_anchor"（入口页直接列型号）、
"dom_anchor_two_level"（入口→系列页→型号页两级；松下/夏普这类总览页只列
系列、需再进一层才到型号的站用它）或 "xhr_api"（数据来自官方 JSON 接口，
比 DOM 稳、能一次拿全量，TCL/Samsung/LG 这类 SPA 优先用它）。
用 two_level 时必须给出：
  - series_link_selector / series_url_regex（入口页→系列页，正则 1 个捕获组作系列名）
  - model_link_selector  / model_url_regex （系列页→型号页，正则 1 个捕获组作型号）
用 xhr_api 时必须给出 discover.api：
  - url_template（接口 URL，同源）、list_path（列表点分路径）、model_field（型号字段）；
  - 可选 url_field / model_url_template（详情页 URL）、series_field、
    family_id_field（系列身份键，优先作归并，防同名系列覆盖）。
discover.paginate.type 可选 "js_snippet" 或 "query_param"（或省略=不分页）：
- js_snippet：snippet_ref 只能取以下之一：
  - "numbered_pages_generic"：通用编号页码/下一页累积分页（多数分页站首选）；
  - "philips_paginate"：Philips 专用（aria-label='Show page N of results'）；
- query_param：URL 查询参数翻页（LG ?firstResult=N 型），需给 paginate.param
  （参数名），可选 step（步长，默认 30）/ max_pages（默认 50）/ stop_rounds（默认 2）。
extract_order 只能用 "embedded_json" / "generic_spec_js"。"""


class SpecValidationError(ValueError):
    """生成的 AdapterSpec 未通过校验。"""


# ── 借鉴 Crawl4AI：喂 LLM 前的 HTML 降噪预处理 ────────────────────
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|svg|template)\b[^>]*>.*?</\1>", re.I | re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TAG_ATTR_KEEP = re.compile(
    r"\s+(?!(?:class|id|href|aria-label|data-[\w-]+)\b)[\w:-]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.I)
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


# 生成选择器时保留的属性（其余一律删）；data-* 单独判断保留。
_KEEP_ATTRS = {"id", "class", "name", "type", "value", "href"}


def _prune_html_regex(html: str, max_chars: int) -> str:
    """正则兜底版：lxml 不可用时使用。去脚本/样式/注释与无关属性并截断。"""
    text = str(html or "")
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = re.sub(r"<([\w:-]+)((?:\s+[^>]*?)?)>",
                  lambda m: "<" + m.group(1) + _TAG_ATTR_KEEP.sub("", m.group(2) or "") + ">",
                  text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n<!-- [truncated] -->"
    return text


def prune_html(html: str, max_chars: int = 40000, *,
               text_threshold: int = 2000, attr_value_threshold: int = 500,
               dedupe_repeats: bool = True) -> str:
    """精简 HTML 供 LLM 分析（借鉴 Crawl4AI preprocess_html_for_schema）。

    相比正则版，在 lxml 解析树上操作，更稳、更省 token：
      - 去掉 <head> 整段与 script/style/svg 等非内容标签；
      - 只保留 id/class/name/type/value/href/data-* 这些"生成选择器有用"的属性，
        长属性值截断（但不截会破坏选择器的值时保守处理）；
      - 超长正文文本截断，只留头部（选择器不依赖长正文）；
      - **重复卡片去重**：同 (tag, class, 文本hash) 的重复元素只保留第一个
        ——电视列表页 60 个同构产品卡，喂 LLM 留 1 个样例即可生成选择器，
        其余是纯 token 浪费。这是修复"整页压缩破坏结构/只喂到 1% 页面"的关键。
    lxml 不可用时回退正则版 _prune_html_regex。
    """
    try:
        from lxml import etree, html as lhtml
    except Exception:
        return _prune_html_regex(html, max_chars)

    raw = str(html or "")
    if not raw.strip():
        return ""
    try:
        parser = etree.HTMLParser(remove_comments=True)
        tree = lhtml.fromstring(raw, parser=parser)
    except Exception:
        return _prune_html_regex(html, max_chars)

    # 1) 去 head
    for head in tree.xpath("//head"):
        if head.getparent() is not None:
            head.getparent().remove(head)
    # 2) 删非内容标签
    for tag in ("script", "style", "noscript", "svg", "iframe", "canvas",
                "video", "audio", "source", "track", "map", "area", "template"):
        for el in tree.xpath(f"//{tag}"):
            if el.getparent() is not None:
                el.getparent().remove(el)
    # 3) 清属性 + 截断长文本
    for el in tree.iter():
        if el.getparent() is None:
            continue
        try:
            for attr in list(el.attrib.keys()):
                if not (attr in _KEEP_ATTRS or attr.startswith("data-")):
                    el.attrib.pop(attr, None)
                elif attr not in ("id", "class", "href") and \
                        len(el.attrib.get(attr, "")) > attr_value_threshold:
                    el.attrib[attr] = el.attrib[attr][:attr_value_threshold] + "..."
        except Exception:
            pass
        if el.text and len(el.text.strip()) > text_threshold:
            el.text = el.text.strip()[:text_threshold] + "..."
        if el.tail and len(el.tail.strip()) > text_threshold:
            el.tail = el.tail.strip()[:text_threshold] + "..."
    # 4) 重复卡片去重：同 (tag, class, 文本hash) 只留第一个
    if dedupe_repeats:
        import hashlib
        seen: set[tuple] = set()
        for el in list(tree.xpath("//*[@class]")):
            parent = el.getparent()
            if parent is None:
                continue
            cls = el.get("class") or ""
            if not cls:
                continue
            text_all = "".join(el.itertext())
            sig = (el.tag, cls, hashlib.md5(text_all.encode("utf-8", "replace")).hexdigest())
            if sig in seen:
                parent.remove(el)
            else:
                seen.add(sig)

    try:
        result = etree.tostring(tree, encoding="unicode", method="html")
    except Exception:
        return _prune_html_regex(html, max_chars)
    result = _WS_RE.sub(" ", result)
    result = _BLANK_LINES_RE.sub("\n", result).strip()
    if len(result) > max_chars:
        result = result[:max_chars] + "\n<!-- [truncated] -->"
    return result


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON 对象，容忍 ```json 包裹与前后噪声。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    # 直接尝试
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    # 兜底：截取第一个 { 到最后一个 } 的平衡片段
    start = raw.find("{")
    end = raw.rfind("}")
    if 0 <= start < end:
        try:
            value = json.loads(raw[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise SpecValidationError("模型输出无法解析为 JSON 对象")


def _same_or_allowed_host(url: str, allowed_hosts: set[str]) -> bool:
    host = urlsplit(str(url or "")).netloc.lower()
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in allowed_hosts)


def validate_spec(spec: dict, probe_report: dict | None = None) -> list[str]:
    """校验 AdapterSpec，返回问题列表（空列表=通过）。不抛异常，供调用方决定。"""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["Spec 不是 JSON 对象"]

    for key in ("code", "entry_url", "base_url"):
        if not str(spec.get(key) or "").strip():
            problems.append(f"缺少必填字段: {key}")

    discover = spec.get("discover") or {}
    mode = str(discover.get("mode") or "")
    if mode not in SUPPORTED_DISCOVER_MODES:
        problems.append(f"discover.mode 不支持: {mode!r}（引擎仅实现 {sorted(SUPPORTED_DISCOVER_MODES)}）")

    # xhr_api 型号来自接口字段，不要求 model_url_regex；其余模式必填且需 1 捕获组
    if mode != "xhr_api":
        regex = str(discover.get("model_url_regex") or "")
        if not regex:
            problems.append("discover.model_url_regex 为空")
        else:
            try:
                compiled = re.compile(regex)
                if compiled.groups < 1:
                    problems.append("discover.model_url_regex 需要至少 1 个捕获组（型号）")
            except re.error as exc:
                problems.append(f"discover.model_url_regex 无法编译: {exc}")

    # xhr_api 额外要求：api.url_template / list_path / model_field 必填
    if mode == "xhr_api":
        api = discover.get("api") or {}
        for f in ("url_template", "list_path", "model_field"):
            if not str(api.get(f) or "").strip():
                problems.append(f"xhr_api 缺 discover.api.{f}")

    # 两级发现额外要求：系列层选择器与正则（系列正则需 1 个捕获组作系列名）
    if mode == "dom_anchor_two_level":
        if not str(discover.get("series_link_selector") or "").strip():
            problems.append("dom_anchor_two_level 缺 discover.series_link_selector")
        s_regex = str(discover.get("series_url_regex") or "")
        if not s_regex:
            problems.append("dom_anchor_two_level 缺 discover.series_url_regex")
        else:
            try:
                if re.compile(s_regex).groups < 1:
                    problems.append("discover.series_url_regex 需要至少 1 个捕获组（系列名）")
            except re.error as exc:
                problems.append(f"discover.series_url_regex 无法编译: {exc}")
        if not str(discover.get("model_link_selector") or "").strip():
            problems.append("dom_anchor_two_level 缺 discover.model_link_selector")

    paginate = discover.get("paginate") or {}
    ptype = str(paginate.get("type") or "")
    if ptype == "js_snippet":
        ref = str(paginate.get("snippet_ref") or "")
        if ref not in KNOWN_SNIPPETS:
            problems.append(f"paginate.snippet_ref 未登记: {ref!r}（可用 {sorted(KNOWN_SNIPPETS)}）")
    elif ptype == "query_param":
        if not str(paginate.get("param") or "").strip():
            problems.append("paginate.type=query_param 缺 paginate.param")
    elif ptype and ptype not in ("js_snippet", "query_param"):
        problems.append(f"paginate.type 不支持: {ptype!r}（可用 js_snippet / query_param）")

    spec_cfg = spec.get("spec") or {}
    order = spec_cfg.get("extract_order") or []
    if not isinstance(order, list) or not order:
        problems.append("spec.extract_order 为空")
    else:
        for method in order:
            if method not in SUPPORTED_EXTRACT_METHODS:
                problems.append(f"spec.extract_order 含不支持方法: {method!r}")

    # 域名同源/白名单：防 LLM 把入口/接口指向站外
    allowed_hosts: set[str] = set()
    base_host = urlsplit(str(spec.get("base_url") or "")).netloc.lower()
    if base_host:
        allowed_hosts.add(base_host)
    if probe_report:
        for u in [probe_report.get("entry_url")] + list(probe_report.get("candidate_hosts") or []):
            h = urlsplit(str(u or "")).netloc.lower()
            if h:
                allowed_hosts.add(h)
    for url_field in [spec.get("entry_url"), *(spec.get("fallback_entry_urls") or [])]:
        if url_field and allowed_hosts and not _same_or_allowed_host(url_field, allowed_hosts):
            problems.append(f"URL 越出允许域名: {url_field}")
    api = discover.get("api") or {}
    if api.get("url_template") and allowed_hosts:
        if not _same_or_allowed_host(api["url_template"], allowed_hosts):
            problems.append(f"api.url_template 越出允许域名: {api['url_template']}")

    return problems


# ── 借鉴 hanzheng schema_validator：在真实 HTML 上做命中率+语义+覆盖打分 ──
# 与 hanzheng 的区别：他们打分商品页"字段"（title/price）；我们打分"发现结果"
# （产品链接命中、型号可提取、型号格式合理、找全率 vs expected），服务于发现阶段
# "摸清页面结构、找全型号"的目标。生成一份可复用的 AdapterSpec 供之后抓取。

_MODEL_TOKEN_RE = re.compile(r"[A-Za-z].*\d|\d.*[A-Za-z]")  # 型号通常字母数字混合


def _norm_model(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", str(value or "").upper())


def score_spec_on_html(spec: dict, entry_html: str, *,
                       sample_specs: list[list[str]] | None = None,
                       threshold: float = 70.0) -> dict:
    """用真实入口页 HTML 给 AdapterSpec 的发现能力打分（命中率+语义+覆盖）。

    参数：
        spec: 待评分的 AdapterSpec
        entry_html: 入口列表页的真实 HTML（静态或已渲染）
        sample_specs: 可选，某型号页解析出的 (区分,项目,值) 三元组，用于给 spec 段打分
        threshold: 通过分数线

    返回 dict：{score, passed, checks, failures, discovered_models, coverage}
    checks 为每个维度的 {name, hit, note, weight}；failures 供回喂 LLM。
    结构上对齐 hanzheng ValidationReport 的用法，但维度面向"发现/找全"。
    """
    from .fetchers import LxmlDom

    checks: list[dict] = []
    failures: list[str] = []
    discover = spec.get("discover") or {}
    link_selector = str(discover.get("link_selector") or "")
    model_regex_raw = str(discover.get("model_url_regex") or "")
    expected = (spec.get("expected") or {})
    expected_models = expected.get("model_count")

    dom = LxmlDom.parse(entry_html or "")
    if dom is None:
        return {"score": 0.0, "passed": False, "checks": [],
                "failures": ["lxml 未安装或入口 HTML 解析失败，无法实测打分"],
                "discovered_models": 0, "coverage": None}

    # xhr_api：数据来自 JSON 接口，与入口页 HTML 无关，无法用本函数打分。
    # 找全率必须靠引擎实际请求接口后看审计 item_count / discovered_model_count。
    if str(discover.get("mode") or "") == "xhr_api":
        api = discover.get("api") or {}
        cfg_ok = all(str(api.get(f) or "").strip()
                     for f in ("url_template", "list_path", "model_field"))
        checks.append({"name": "xhr_api 配置完整", "hit": cfg_ok,
                       "note": "url_template/list_path/model_field 齐备", "weight": 3.0})
        if not cfg_ok:
            failures.append("xhr_api 配置不完整（需 url_template/list_path/model_field）")
        return {
            "score": 100.0 if cfg_ok else 0.0,
            "passed": cfg_ok,
            "checks": checks,
            "failures": failures,
            "discovered_models": None,
            "coverage": None,
            "note": "xhr_api 找全率需 spec-crawl 后看审计 item_count / discovered_model_count 判定",
        }

    # 两级发现（dom_anchor_two_level）：入口页只列系列、不列型号，
    # 用 series_link_selector + series_url_regex 评估"系列层"能否发现；
    # 型号找全率无法从单张入口 HTML 判定，必须靠引擎实际执行两级抓取
    # （spec-crawl 后看审计的 discovered_model_count），此处如实标注不否决。
    if str(discover.get("mode") or "") == "dom_anchor_two_level":
        s_sel = str(discover.get("series_link_selector") or "")
        s_regex_raw = str(discover.get("series_url_regex") or "")
        s_hrefs: list[str] = []
        if s_sel:
            try:
                s_hrefs = dom.attr_list(s_sel, "href", limit=2000)
            except Exception:
                s_hrefs = []
        series_found: set[str] = set()
        if s_regex_raw:
            try:
                s_re = re.compile(s_regex_raw, re.I)
                for h in s_hrefs:
                    m = s_re.search(h or "")
                    if m:
                        series_found.add(m.group(1).upper())
            except re.error as exc:
                failures.append(f"series_url_regex 无法编译: {exc}")
        sel_hit = len(s_hrefs) > 0
        series_hit = len(series_found) > 0
        checks.append({"name": "series_link_selector 命中", "hit": sel_hit,
                       "note": f"入口页命中 {len(s_hrefs)} 个系列候选链接", "weight": 3.0})
        checks.append({"name": "系列可提取", "hit": series_hit,
                       "note": f"提取到 {len(series_found)} 个系列", "weight": 3.0})
        if not sel_hit:
            failures.append("series_link_selector 未命中：选择器错误或入口页未渲染完")
        if not series_hit:
            failures.append("series_url_regex 提不到系列：正则或捕获组不对")
        passed = sel_hit and series_hit
        return {
            "score": 100.0 if passed else 0.0,
            "passed": passed,
            "checks": checks,
            "failures": failures,
            "discovered_series": len(series_found),
            "discovered_series_sample": sorted(series_found)[:20],
            "discovered_models": None,
            "coverage": None,
            "note": "两级站型号找全率需 spec-crawl 后看审计 discovered_model_count 判定",
        }

    # 维度1：link_selector 命中产品链接（权重 3）
    hrefs: list[str] = []
    if link_selector:
        try:
            hrefs = dom.attr_list(link_selector, "href", limit=2000)
        except Exception:
            hrefs = []
    link_hit = len(hrefs) > 0
    checks.append({"name": "link_selector 命中", "hit": link_hit,
                   "note": f"命中 {len(hrefs)} 个链接", "weight": 3.0})
    if not link_hit:
        failures.append(f"discover.link_selector 未命中任何链接（selector={link_selector!r}）")

    # 维度2：型号正则可从链接提取型号，且格式合理（权重 3）
    models: list[str] = []
    regex_ok = False
    if model_regex_raw:
        try:
            model_regex = re.compile(model_regex_raw, re.I)
            regex_ok = True
        except re.error as exc:
            failures.append(f"discover.model_url_regex 无法编译: {exc}")
            model_regex = None
    else:
        model_regex = None
        failures.append("discover.model_url_regex 为空")
    if model_regex is not None:
        for href in hrefs:
            m = model_regex.search(href)
            if m and m.groups():
                token = m.group(1)
                if _MODEL_TOKEN_RE.search(token):  # 语义：型号应字母数字混合
                    norm = _norm_model(token)
                    if norm and norm not in models:
                        models.append(norm)
    model_ok = len(models) > 0
    checks.append({"name": "型号可提取且格式合理", "hit": model_ok,
                   "note": f"提取到 {len(models)} 个不同型号", "weight": 3.0})
    if not model_ok:
        failures.append("model_url_regex 未能从命中的链接提取出格式合理的型号"
                        "（型号应含字母+数字）")

    # 维度3：找全率——发现型号数 vs expected（权重 2；无 expected 时只要 >0）
    coverage = None
    if expected_models:
        coverage = len(models) / max(1, int(expected_models))
        cov_ok = coverage >= 0.8  # 找全 80% 以上视为合格
        checks.append({"name": f"找全率(vs 期望{expected_models})", "hit": cov_ok,
                       "note": f"{len(models)}/{expected_models} = {coverage:.0%}",
                       "weight": 2.0})
        if not cov_ok:
            failures.append(f"发现型号数 {len(models)} 明显少于期望 {expected_models}"
                            f"（找全率 {coverage:.0%}），link_selector/分页可能漏抓")
    else:
        checks.append({"name": "发现型号数>0", "hit": model_ok,
                       "note": f"{len(models)} 个（无期望值参考）", "weight": 2.0})

    # 维度4（可选）：spec 段能出规格行（权重 2）
    if sample_specs is not None:
        spec_ok = len([t for t in sample_specs if t and len(t) >= 2 and str(t[1]).strip()]) >= 3
        checks.append({"name": "型号页规格可解析", "hit": spec_ok,
                       "note": f"样本解析出 {len(sample_specs)} 条规格", "weight": 2.0})
        if not spec_ok:
            failures.append("spec 段在型号页样本上解析出的规格行过少（<3），"
                            "extract_order/embedded_json 字段可能不匹配")

    # 加权打分：命中率 60% + 语义/覆盖 40%（对齐 hanzheng 的加权思路）
    total_w = sum(c["weight"] for c in checks) or 1.0
    hit_w = sum(c["weight"] for c in checks if c["hit"])
    score = hit_w / total_w * 100.0
    score = max(0.0, min(100.0, score))
    # 发现阶段硬指标否决：link 未命中、型号提取不到、找全率不足，任一都直接判不通过。
    # 借鉴 hanzheng"必填字段未命中扣分"，但对发现阶段用更强的否决——因为"找全型号"
    # 是发现阶段的核心目标，不能被其它维度的高分稀释掉。
    veto = False
    if not link_hit or not model_ok:
        veto = True
    if coverage is not None and coverage < 0.8:
        veto = True
    return {
        "score": score,
        "passed": (score >= threshold) and not veto,
        "checks": checks,
        "failures": failures,
        "discovered_models": len(models),
        "coverage": coverage,
        "discovered_model_list": models,
    }


def build_generate_messages(probe_report: dict) -> list[dict[str, str]]:
    report_json = json.dumps(probe_report, ensure_ascii=False, indent=2)
    # 从探测报告提炼给 LLM 的关键线索（发现阶段：最佳选择器 / URL 模式 / 分页方式）
    summary = probe_report.get("entry_dom_summary") or {}
    hint_lines = []
    if summary.get("best_link_selector"):
        hint_lines.append(f"- 探测到最佳产品链接选择器: {summary['best_link_selector']} "
                          f"（命中 {summary.get('static_unique_links', 0)} 个链接）")
    if summary.get("sample_product_hrefs"):
        hint_lines.append(f"- 产品 URL 样本: {summary['sample_product_hrefs']}")
    if summary.get("product_url_pattern"):
        hint_lines.append(f"- URL 路径模式: {summary['product_url_pattern']} "
                          f"（据此写 model_url_regex，用 1 个捕获组框住型号段）")
    load_hint = summary.get("load_more_hint")
    pager = summary.get("pager") or {}
    if load_hint == "scroll":
        hint_lines.append("- 该页面滚动后链接增多：设 fetch.scroll_until_stable=true")
    elif load_hint == "click_more_or_paginate":
        hint_lines.append("- 该页面点击加载更多/翻页后链接增多：优先设 "
                          "discover.paginate（js_snippet 或后续引擎支持的翻页），并配 scroll_until_stable")
    elif load_hint in ("paginate", "paginate_replace"):
        detail = ("检测到编号页码按钮（Show page N）" if pager.get("has_numbered_pages")
                  else "检测到下一页按钮")
        hint_lines.append(
            f"- 该站是分页站（{detail}），首屏只含部分型号，其余需逐页点击累积。"
            "请设 discover.paginate 为 js_snippet，snippet_ref 首选 "
            "\"numbered_pages_generic\"（通用编号页码/下一页累积，适配大多数分页站）；"
            "仅 Philips 官网可用专用的 \"philips_paginate\"。")
    elif load_hint == "none":
        # 明确告知：首屏即全部，不要加分页。否则 LLM 常因报告里 pager.has_next_button
        # （可能来自轮播/推荐区的 next 按钮）自作主张加 discover.paginate，反而在静态站
        # 点错按钮导致发现结果异常（Sony BRAVIA lineup 即此坑）。
        hint_lines.append(
            "- 该站首屏即列出全部型号（滚动/交互后链接数无增长，且无编号页码）："
            "**不要**设 discover.paginate；即便报告里 pager.has_next_button 为真，"
            "那多半是轮播/推荐区按钮，与型号分页无关，请忽略。")
    hints = ("\n探测线索（请优先据此填 discover.link_selector / model_url_regex / 分页）：\n"
             + "\n".join(hint_lines)) if hint_lines else ""
    user = (
        f"{_SCHEMA_HINT}\n\n"
        f"探测报告（probe_report）：\n{report_json}\n{hints}\n\n"
        "请只输出符合上述 schema 的 AdapterSpec JSON。"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_repair_messages(probe_report: dict, prev_spec: dict,
                          problems: list[str]) -> list[dict[str, str]]:
    user = (
        f"{_SCHEMA_HINT}\n\n"
        f"探测报告：\n{json.dumps(probe_report, ensure_ascii=False)}\n\n"
        f"上一版 AdapterSpec：\n{json.dumps(prev_spec, ensure_ascii=False)}\n\n"
        f"该版本存在以下问题，请针对性修正后重新输出完整的 AdapterSpec JSON：\n"
        + "\n".join(f"- {p}" for p in problems)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ── 借鉴 Crawl4AI generate_schema：喂真实样本页生成抽取配置 ─────────
_SPEC_FROM_SAMPLE_SYSTEM = (
    "你是网页规格抽取配置生成器。根据给定的（已精简的）型号规格页 HTML 样本，"
    "判断规格数据的承载形式，只输出一份 JSON 的 spec 抽取配置，不要输出解释或代码块标记。"
    "你只产出配置，绝不产出可执行代码。"
)

_SPEC_FROM_SAMPLE_HINT = """请只输出如下形状的 JSON（spec 抽取配置）：
{
  "extract_order": ["embedded_json", "generic_spec_js"],
  "embedded_json": {
    "root_marker": "<页面内嵌 JSON 中承载规格的键名，如 specification>",
    "chapters_field": "<分组数组字段名>",
    "chapter_name_field": "<分组名字段>",
    "items_field": "<条目数组字段名>",
    "item_name_field": "<条目名字段>",
    "values_field": "<取值数组字段名>",
    "value_name_field": "<取值名字段>",
    "value_join": " / "
  }
}
判断规则：
- 若样本 HTML 中存在承载完整规格的内嵌 JSON（如 "specification":{...csChapter...}），
  则 extract_order 首选 "embedded_json" 并填 embedded_json 字段名；
- 若规格以 DOM 表格/dl/键值对呈现、无稳定内嵌 JSON，则 extract_order 用 ["generic_spec_js"]，
  可省略 embedded_json。
只能使用 "embedded_json" / "generic_spec_js" 两种方法。"""


def generate_spec_from_sample(sample_html: str, client: LLMClient | None = None,
                              max_chars: int = 40000) -> dict:
    """借鉴 Crawl4AI generate_schema：由真实型号页样本生成 spec 抽取配置。

    返回一个 spec 子配置 dict（可直接放进 AdapterSpec 的 "spec" 字段）。
    仅生成配置，不执行抽取；生成结果仍需经引擎小样验证。
    """
    client = client or LLMClient()
    if not client.configured:
        raise LLMUnavailable("LLM 未配置，无法从样本生成抽取配置")
    pruned = prune_html(sample_html, max_chars=max_chars)
    messages = [
        {"role": "system", "content": _SPEC_FROM_SAMPLE_SYSTEM},
        {"role": "user", "content": f"{_SPEC_FROM_SAMPLE_HINT}\n\nHTML 样本：\n{pruned}"},
    ]
    text = client.chat(messages)
    spec_cfg = _extract_json(text)
    order = spec_cfg.get("extract_order") or []
    if not isinstance(order, list) or not order:
        raise SpecValidationError("样本生成的 spec 配置缺少 extract_order")
    for method in order:
        if method not in SUPPORTED_EXTRACT_METHODS:
            raise SpecValidationError(f"样本生成的 extract_order 含不支持方法: {method!r}")
    return spec_cfg


def onboard_from_html(entry_url: str, entry_html: str, *,
                      code: str = "", brand_name: str = "", region: str = "",
                      expected_model_count: int | None = None,
                      client: LLMClient | None = None, max_repair: int = 3,
                      score_threshold: float = 70.0) -> dict:
    """发现阶段一站式编排（离线/在线通用）：Probe → Generate → Verify → Repair。

    给入口页 HTML（在线时由 spec_probe.probe_site 提供），自动：
      1. build_probe_report 摸清结构（最佳链接选择器、URL 模式、分页方式）；
      2. LLM 生成 AdapterSpec；
      3. 静态校验 + 在真实 entry_html 上实测打分（命中/找全率），不达标回喂修复；
      4. 返回 {report(probe), spec, problems, passed}。

    passed=True 时 spec 即可保存为该站 schema，之后按它直接抓全站 spec。
    """
    from .spec_probe import build_probe_report

    probe = build_probe_report(
        entry_url, entry_html, code=code, brand_name=brand_name, region=region,
        expected_model_count=expected_model_count,
        after_scroll_html=entry_html)
    spec, problems = generate_spec(
        probe, client=client, max_repair=max_repair,
        entry_html=entry_html, score_threshold=score_threshold)
    return {
        "probe_report": probe,
        "spec": spec,
        "problems": problems,
        "passed": not problems,
    }


def generate_spec(probe_report: dict, client: LLMClient | None = None,
                  max_repair: int = 3, entry_html: str = "",
                  score_threshold: float = 70.0,
                  sample_specs: list[list[str]] | None = None
                  ) -> tuple[dict, list[str]]:
    """调用 LLM 生成 AdapterSpec；不达标则回喂问题修复，最多 max_repair 轮。

    校验分两层：
      1) validate_spec：静态校验（字段/正则/域名/白名单）——始终执行；
      2) score_spec_on_html：若提供 entry_html，则在真实页面上实测打分
         （命中率+语义+找全率），低于 score_threshold 也回喂修复。
    实测打分是吸收 hanzheng 的核心——不仅"配置合法"，还要"在真实页面上真能
    发现型号并找全"，直接服务发现阶段目标。

    返回 (spec, problems)。problems 为空表示两层校验都通过。
    """
    client = client or LLMClient()
    if not client.configured:
        raise LLMUnavailable("LLM 未配置，无法生成 Spec")

    messages = build_generate_messages(probe_report)
    spec: dict = {}
    problems: list[str] = ["未生成"]
    for attempt in range(max_repair + 1):
        text = client.chat(messages)
        try:
            spec = _extract_json(text)
        except SpecValidationError as exc:
            problems = [str(exc)]
            messages = build_repair_messages(probe_report, {}, problems)
            continue

        problems = validate_spec(spec, probe_report)
        # 静态校验通过且提供了真实 HTML → 再做实测打分
        if not problems and entry_html:
            report = score_spec_on_html(spec, entry_html,
                                        sample_specs=sample_specs,
                                        threshold=score_threshold)
            if not report["passed"]:
                problems = [f"实测打分 {report['score']:.0f}/{score_threshold:.0f} 未达标"] \
                    + report["failures"]
        if not problems:
            return spec, []
        messages = build_repair_messages(probe_report, spec if isinstance(spec, dict) else {}, problems)
    return spec, problems
