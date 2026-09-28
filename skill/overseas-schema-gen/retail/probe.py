#!/usr/bin/env python
"""retail-schema-gen skill 探测入口。

包装 `overseas.retail_probe`（探测器）与 `overseas.retail_generator`（生成器），
供智能体用真实型号探一个陌生零售站的结构：

用法：
    # 1) 探搜索页 + 商品页，输出结构化报告（在线，需 BrowserFetcher/代理出海）
    py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py \
        --site coppel_mx --model "75U6SV" --brand Hisense \
        --base-url https://www.coppel.com

    # 2) 静态校验一份 RetailSpec 草稿
    py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --verify data/schema_drafts/x.retail.json

    # 3) 从探测信号生成离线模板 spec（无 LLM 快速起步）
    py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --site x_mx --template

退出码：0 探测成功 / 校验通过；1 有问题（输出里已有原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 项目根：.kiro/skills/overseas-schema-gen/retail/probe.py → parents[4]
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROBE_DIR = ROOT / "data" / "probe"


def _p(*a) -> None:
    print(*a, flush=True)


def _save(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fetcher():
    from overseas import config
    from overseas.fetchers import BrowserFetcher
    from overseas.infra import RateLimiter
    config.ensure_dirs()
    return BrowserFetcher(proxy=config.PROXY or "",
                          rate_limiter=RateLimiter(min_interval=2.0))


def cmd_probe(args) -> int:
    """模式一：在线探测搜索页 + 商品页。"""
    from overseas.retail_probe import probe_retail_site, _norm_model

    fetcher = _make_fetcher()
    try:
        report, best_html = probe_retail_site(
            fetcher, args.site, args.model,
            base_url=args.base_url, brand=args.brand or "",
            search_path=args.search_path or "", want_reviews=True)
    finally:
        close = getattr(fetcher, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    html_path = PROBE_DIR / f"{args.site}_search.html"
    if best_html:
        _save(html_path, best_html)
        report["_saved_search_html"] = str(html_path.relative_to(ROOT))
        report["_search_html_chars"] = len(best_html)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    _print_hints(report)
    return 0


def _print_hints(report: dict) -> None:
    """把关键信号翻成"下一步该填什么"，减少智能体误读。"""
    s = report.get("search") or {}
    pp = report.get("product_page") or {}
    lines = ["", "=" * 60, "读信号提示（据此填 search / match / price / reviews / shops）：", "=" * 60]
    if s.get("best_candidate_selector"):
        lines.append(f"搜索候选容器: {s['best_candidate_selector']}  "
                     f"(命中 {s.get('result_count', 0)} 个，型号纯度 {s.get('purity', 0):.2f})")
        lines.append("  → 写入 search.container；purity<0.3 说明混入非目标，考虑收窄。")
    if s.get("sample_pdp_hrefs"):
        lines.append(f"商品 URL 样本: {s['sample_pdp_hrefs']}")
    if pp.get("h1"):
        lines.append(f"商品页 h1: {pp['h1'][:60]}  model_in_h1={pp.get('model_in_h1')}")
        lines.append("  → product.title 用 h1; product.size 用 h1 + size_inch（兜底）。")
    price_sels = (pp.get("detected") or {}).get("price_selectors") or []
    if price_sels:
        sel = price_sels[0]["selector"]
        lines.append(f"价格选择器候选: {sel}  样本: {price_sels[0].get('sample', '')}")
        lines.append("  → price.price 用 transform money; raw_text 可同选择器。")
    rv = report.get("reviews") or {}
    if rv.get("pagination_type"):
        lines.append(f"评价翻页: {rv['pagination_type']}  → paginate.reviews.type={rv['pagination_type']}")
    sh = report.get("shops") or {}
    if sh.get("mode"):
        lines.append(f"多店铺判定: {sh['mode']}  → shops.mode={sh['mode']}")
    if not s.get("best_candidate_selector"):
        lines.append("\n⚠ 搜索页无候选：可能 ① 该站无此型号 ② 搜索直达 PDP（302 单结果）"
                     " ③ 选择器候选没覆盖。用带品牌关键词重试或换已知在售型号。")
    lines.append("=" * 60)
    print("\n".join(lines))


def cmd_verify(args) -> int:
    """模式 2：静态校验一份 RetailSpec 草稿。"""
    from overseas.retail_spec import load_retail_spec, validate_retail_spec
    try:
        spec = load_retail_spec(args.spec)
    except Exception as e:
        _p(f"[FAIL] {args.spec} 加载失败: {e}")
        return 1
    problems = validate_retail_spec(spec)
    if problems:
        _p(f"[FAIL] {args.spec} 校验不过 ({len(problems)} 项):")
        for p in problems:
            _p(f"  - {p}")
        return 1
    _p(f"[PASS] {args.spec} 静态校验通过（capabilities={spec.get('capabilities')} "
       f"shops.mode={spec.get('shops', {}).get('mode')}）")
    return 0


def cmd_template(args) -> int:
    """模式 3：离线生成一份模板 RetailSpec（无 LLM 快速起步）。"""
    from overseas.retail_generator import spec_from_probe_report
    from overseas.retail_spec import validate_retail_spec
    report = {"code": args.code, "brand": args.brand or args.code,
              "base_url": args.base_url or "", "region": args.region or "mx",
              "search": {"best_candidate_selector": ""}}
    spec = spec_from_probe_report(report)
    problems = validate_retail_spec(spec)
    out = args.out or ROOT / "data" / "schema_drafts" / f"{args.code}.retail.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    _p(f"[OUT] 模板 spec 已写: {out}（校验 {'ok' if not problems else problems}）")
    return 0 if not problems else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="retail-probe", description="零售站 RetailSpec 生成 skill 探测入口")
    sub = ap.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("probe", help="在线探测一个零售站（搜索 + 商品页）")
    s.add_argument("--site", required=True, help="站点 code，如 coppel_mx")
    s.add_argument("--model", required=True, help="要探测的真实型号，如 75U6SV")
    s.add_argument("--brand", default="", help="品牌名（搜索关键词用）")
    s.add_argument("--base-url", required=True, help="站点 base URL")
    s.add_argument("--search-path", default="", help="搜索路径（可选，如 sd/）")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("verify", help="静态校验 RetailSpec 草稿")
    s.add_argument("--spec", required=True, help="RetailSpec JSON 路径")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("template", help="离线生成模板 RetailSpec")
    s.add_argument("--code", required=True, help="站点 code")
    s.add_argument("--brand", default="")
    s.add_argument("--base-url", default="", help="站点 base URL")
    s.add_argument("--region", default="mx")
    s.add_argument("--out", default="", help="输出路径（默认 data/schema_drafts/）")
    s.set_defaults(func=cmd_template)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _p("\n已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())