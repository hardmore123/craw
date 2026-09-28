#!/usr/bin/env python
"""spec-schema-gen skill 的强探测脚本。

复用项目现成能力（BrowserFetcher / spec_probe / spec_generator / generic_spec），
不重写任何探测或校验逻辑——本脚本只做"取信号、摆出来给智能体看"和"跑校验"。

三种模式：
    --entry  <url>      探入口页：产品链接选择器排名、URL 模式、分页信号
    --model  <url>      探型号页：规格载体判断（内嵌 JSON / DOM 表格）+ 容器样本
    --verify <spec.json> 校验 schema：静态校验 + 真实页面实测打分

用法示例：
    py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --entry "https://x.com/tv" --code x_ca --expect-models 25
    py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --model "https://x.com/tv/65U8N"
    py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --verify data/schema_drafts/x_ca.spec.json --entry "https://x.com/tv"

注意：Windows PowerShell 下管道会吞 stderr，排查异常请加 2>&1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 项目根：.kiro/skills/overseas-schema-gen/spec/probe.py → parents[4]
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文/日文输出会炸，强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROBE_DIR = ROOT / "data" / "probe"
DRAFT_DIR = ROOT / "data" / "schema_drafts"

# 型号页里常见的"规格内嵌 JSON"根键名候选，用于判断 extract_order 该选哪档。
_JSON_MARKERS = [
    "specification", "csChapter", "classifications", "techSpecs",
    "technicalSpecs", "productSpecs", "specifications", "attributeGroups",
    "featureBullets", "specGroups",
]

# 型号页规格容器的通用候选选择器（只用于"摆样本给智能体看"）。
_SPEC_CONTAINER_CANDIDATES = [
    "table", "dl",
    "[class*='spec' i]", "[class*='Spec' i]",
    "[id*='spec' i]",
    "[class*='tech' i]", "[class*='detail' i]",
    "[class*='feature' i]", "[class*='attribute' i]",
]


class _StubAdapter:
    """make_fetcher / open_dom 只需要这几个属性，不必造真适配器。"""

    code = "probe"
    name = "probe"
    base_url = ""
    requires_browser = True
    suggested_interval = 4.0
    spec_entry_wait = "body"
    spec_page_wait = "body"
    spec_entry_extra_wait_ms = 4000
    spec_entry_scroll_until_stable = False
    spec_entry_scroll_growth_selector = ""
    spec_entry_scroll_max_passes = 40
    spec_wait_until = "domcontentloaded"


def _make_browser_fetcher():
    from overseas.scenarios import make_fetcher
    return make_fetcher(_StubAdapter(), force_browser=True)


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ─────────────────────────────── 模式一：探入口页 ───────────────────────────────
def cmd_entry(args) -> int:
    from overseas import config
    from overseas.spec_probe import probe_site

    config.ensure_dirs()
    code = args.code or "unknown"
    fetcher = _make_browser_fetcher()
    try:
        report, best_html = probe_site(
            fetcher, args.entry,
            code=code, brand_name=args.brand or code, region=args.region or "",
            expected_model_count=args.expect_models,
            wait_selector=args.wait or "a[href]",
            scroll_passes=args.scroll_passes,
            try_load_more=not args.no_load_more,
        )
    finally:
        close = getattr(fetcher, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    # 存渲染后 HTML，供 --verify 实测打分复用，避免重复联网。
    html_path = PROBE_DIR / f"{code}_entry.html"
    if best_html:
        _save(html_path, best_html)
        report["_saved_entry_html"] = str(html_path.relative_to(ROOT))
        report["_entry_html_chars"] = len(best_html)

    _dump(report)
    _print_entry_hints(report)
    return 0


def _print_entry_hints(report: dict) -> None:
    """把关键信号翻成"下一步该怎么填"，减少智能体误读。"""
    summary = report.get("entry_dom_summary") or {}
    hint = str(summary.get("load_more_hint") or "none")
    pager = summary.get("pager") or {}
    lines = ["", "=" * 60, "读信号提示（据此填 discover / fetch）：", "=" * 60]

    ranking = summary.get("link_selector_ranking") or []
    if ranking:
        lines.append("链接选择器候选（看 model_like_ratio 纯度，别只看命中数）：")
        for item in ranking[:5]:
            lines.append(
                f"  {item.get('selector'):<40} 命中={item.get('unique_links'):<5}"
                f" 纯度={item.get('model_like_ratio')}")

    lines.append(f"\nload_more_hint = {hint}")
    if hint == "none":
        lines.append("  → 静态列全：不要写 discover.paginate。")
        if pager.get("has_next_button") and not pager.get("has_numbered_pages"):
            lines.append("  ⚠ 检测到孤立 next 按钮但无编号页码，那多半是轮播，忽略它。")
        lines.append("  → 套路见 playbook/static_lineup.md")
    elif hint == "scroll":
        lines.append("  → 懒加载：fetch.scroll_until_stable=true + scroll_growth_selector=链接选择器。")
        lines.append("  → 套路见 playbook/lazy_scroll.md")
    elif hint in ("click_more_or_paginate", "paginate_replace"):
        lines.append("  → 分页站：discover.paginate = {type:js_snippet, snippet_ref:numbered_pages_generic}")
        lines.append("  → 套路见 playbook/numbered_paginate.md")

    expected = (report.get("expected") or {}).get("model_count")
    static_n = summary.get("static_unique_links")
    if expected and static_n is not None and static_n < expected:
        lines.append(f"\n⚠ 首屏仅 {static_n} 个链接 < 期望 {expected}，"
                     "必须配对分页/懒加载才可能找全。")
    lines.append("=" * 60)
    print("\n".join(lines))


# ─────────────────────────────── 模式二：探型号页 ───────────────────────────────
def cmd_model(args) -> int:
    from overseas import config
    from overseas.scenarios import open_dom
    from overseas.spec_parser import GENERIC_SPEC_JS

    config.ensure_dirs()
    fetcher = _make_browser_fetcher()
    out: dict = {"model_url": args.model}
    try:
        with open_dom(_StubAdapter(), fetcher, args.model,
                      wait_selector=args.wait or "body") as (res, dom, html):
            out["http_status"] = getattr(res, "status", 0)
            out["ok"] = bool(getattr(res, "ok", False))
            if not html:
                out["error"] = "页面未取到 HTML"
                _dump(out)
                return 1
            out["html_chars"] = len(html)

            # 1) 内嵌 JSON 线索：哪些候选根键名出现了、出现几次
            markers = {}
            for name in _JSON_MARKERS:
                n = len(re.findall(rf'["\\]*{re.escape(name)}["\\]*\s*:', html))
                if n:
                    markers[name] = n
            out["embedded_json_markers"] = markers

            # 2) 通用 DOM 抽取实际能出多少行（判断 generic_spec_js 是否够用）
            eval_js = getattr(dom, "eval_js", None)
            triples = []
            if callable(eval_js):
                try:
                    triples = eval_js(GENERIC_SPEC_JS) or []
                except Exception as exc:
                    out["generic_spec_js_error"] = f"{type(exc).__name__}: {exc}"
            out["generic_spec_js_rows"] = len(triples)
            out["generic_spec_js_sample"] = triples[:12]

            # 3) 规格容器候选命中数 + 少量样本，供智能体挑 page_wait
            containers = []
            for sel in _SPEC_CONTAINER_CANDIDATES:
                try:
                    n = len(dom.sub(sel, limit=200))
                except Exception:
                    continue
                if n:
                    containers.append({"selector": sel, "count": n})
            containers.sort(key=lambda x: -x["count"])
            out["spec_container_candidates"] = containers[:8]

            if args.save_html:
                path = PROBE_DIR / f"{args.code or 'model'}_model.html"
                _save(path, html)
                out["_saved_model_html"] = str(path.relative_to(ROOT))
    finally:
        close = getattr(fetcher, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    _dump(out)
    _print_model_hints(out)
    return 0


def _print_model_hints(out: dict) -> None:
    markers = out.get("embedded_json_markers") or {}
    rows = out.get("generic_spec_js_rows") or 0
    lines = ["", "=" * 60, "读信号提示（据此填 spec.extract_order）：", "=" * 60]
    if markers:
        lines.append(f"检测到内嵌 JSON 候选键：{markers}")
        lines.append('  → 建议 extract_order = ["embedded_json", "generic_spec_js"]')
        lines.append("  → 需人工核对真实键名后填 spec.embedded_json（见 playbook/embedded_json.md）")
    else:
        lines.append("未检测到已知内嵌 JSON 键。")
    lines.append(f"\ngeneric_spec_js 实测抽出 {rows} 行规格")
    if rows >= 8:
        lines.append('  → 通用 DOM 抽取够用：extract_order = ["generic_spec_js"] 即可')
    elif rows > 0:
        lines.append("  ⚠ 行数偏少，可能只抓到部分表；核对 page_wait 或考虑内嵌 JSON 档")
    else:
        lines.append("  ⚠ 抽不到规格：检查 page_wait 锚点、是否需要点 tab 展开")
        lines.append("  ⚠ 若规格藏在需点击的 tab 里 → 当前引擎无此档位，属能力缺口，记入 cases.jsonl")
    lines.append("=" * 60)
    print("\n".join(lines))


# ─────────────────────────────── 模式三：校验 schema ───────────────────────────────
def cmd_verify(args) -> int:
    from overseas.spec_generator import score_spec_on_html, validate_spec

    spec_path = Path(args.verify)
    if not spec_path.is_absolute():
        spec_path = ROOT / spec_path
    if not spec_path.exists():
        print(f"[ERROR] schema 不存在: {spec_path}")
        return 2
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    code = str(spec.get("code") or args.code or "unknown")

    result: dict = {"schema": str(spec_path), "code": code}

    # 第一层：静态校验
    problems = validate_spec(spec, None)
    result["static_problems"] = problems
    result["static_passed"] = not problems

    # 第二层：实测打分。优先用已保存的入口 HTML，没有就联网重探。
    html = ""
    cached = PROBE_DIR / f"{code}_entry.html"
    if cached.exists() and not args.refetch:
        html = cached.read_text(encoding="utf-8", errors="ignore")
        result["entry_html_source"] = str(cached.relative_to(ROOT))
    elif args.entry:
        from overseas import config
        from overseas.spec_probe import probe_site
        config.ensure_dirs()
        fetcher = _make_browser_fetcher()
        try:
            _, html = probe_site(fetcher, args.entry, code=code,
                                 expected_model_count=(spec.get("expected") or {}).get("model_count"))
        finally:
            close = getattr(fetcher, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        if html:
            _save(cached, html)
        result["entry_html_source"] = f"refetched:{args.entry}"

    if html:
        score = score_spec_on_html(spec, html, threshold=args.threshold)
        # discovered_model_list 可能很长，截断后展示
        models = score.pop("discovered_model_list", [])
        score["discovered_model_sample"] = models[:20]
        result["score_report"] = score
    else:
        result["score_report"] = {"error": "无入口 HTML，无法实测打分。"
                                           "请先跑 --entry，或本次带上 --entry <url>"}

    _dump(result)
    _print_verify_verdict(result)
    return 0


def _print_verify_verdict(result: dict) -> None:
    lines = ["", "=" * 60, "校验结论", "=" * 60]
    if result.get("static_passed"):
        lines.append("[静态校验] 通过")
    else:
        lines.append("[静态校验] 未通过：")
        for p in result.get("static_problems") or []:
            lines.append(f"  - {p}")

    score = result.get("score_report") or {}
    if "error" in score:
        lines.append(f"[实测打分] 跳过：{score['error']}")
    else:
        passed = score.get("passed")
        lines.append(f"[实测打分] {'通过' if passed else '未通过'}"
                     f"  score={score.get('score')}"
                     f"  发现型号={score.get('discovered_models')}"
                     f"  找全率={score.get('coverage')}")
        for f in score.get("failures") or []:
            lines.append(f"  - {f}")
        if not passed:
            lines.append("\n→ 按 SKILL.md Step 5 诊断；每轮只动一个变量。")

    if result.get("static_passed") and score.get("passed"):
        lines.append("\n两层均通过 → 跑真实抓取确认规格行数，然后按 Step 6 收尾沉淀（强制）。")
    lines.append("=" * 60)
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="spec-schema-gen 强探测脚本（探入口 / 探型号页 / 校验 schema）")
    ap.add_argument("--entry", help="入口页 URL（探入口，或 --verify 时用于重新联网取页）")
    ap.add_argument("--model", help="型号页 URL（探规格载体）")
    ap.add_argument("--verify", help="待校验的 AdapterSpec JSON 路径")
    ap.add_argument("--code", default="", help="站点代号，如 philips_ca")
    ap.add_argument("--brand", default="", help="品牌展示名")
    ap.add_argument("--region", default="", help="地区码 ca/jp/mx/us/pe")
    ap.add_argument("--expect-models", type=int, default=None,
                    help="官网标称型号数（找全率闸门的依据，强烈建议填）")
    ap.add_argument("--wait", default="", help="等待锚点选择器")
    ap.add_argument("--scroll-passes", type=int, default=6, help="滚动趟数")
    ap.add_argument("--no-load-more", action="store_true",
                    help="不尝试点击加载更多（只滚动）")
    ap.add_argument("--save-html", action="store_true", help="--model 时保存页面 HTML")
    ap.add_argument("--refetch", action="store_true",
                    help="--verify 时忽略缓存的入口 HTML，重新联网")
    ap.add_argument("--threshold", type=float, default=70.0, help="实测打分通过线")
    args = ap.parse_args()

    if args.verify:
        return cmd_verify(args)
    if args.model:
        return cmd_model(args)
    if args.entry:
        return cmd_entry(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
