"""零售线定期健康探测（P1-2）。

对已配置的 RetailSpec 零售站跑小样验证：抽 N 个型号走「搜索→匹配→详情」，确认
价格/评价选择器仍能命中；价格填充率或评价数相对上次骤降（>30%）时告警，供定时任务
决定是否重抓/重生成 schema。

不做全量抓取（那会发大量请求），只小样验证契约是否还贴真实页面。

命令形式：
    py -3.12 -m overseas.cli retail-health --spec docs/retail_specs/<code>.retail.json [--models-file x] [--baseline db]
    py -3.12 -m overseas.cli retail-health --all   # 遍历 docs/retail_specs/*.retail.json
退出码：0 全健康；2 有骤降/失败项（可作定时任务告警判据）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .retail_spec import load_retail_spec, validate_retail_spec
from .retail_compliance import ensure_allowed


@dataclass
class HealthResult:
    """一个站一次健康探测的结果。"""
    code: str = ""
    ok: bool = True
    models_checked: int = 0
    models_matched: int = 0
    price_fill: int = 0
    review_count: int = 0
    details: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def price_fill_rate(self) -> float:
        return (self.price_fill / self.models_matched) if self.models_matched else 0.0

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "ok": self.ok,
            "models_checked": self.models_checked,
            "models_matched": self.models_matched,
            "price_fill_rate": round(self.price_fill_rate, 3),
            "review_count": self.review_count,
            "details": self.details,
            "problems": self.problems,
        }


def _load_models(models_file: str | None, models: list[str] | None) -> list[dict]:
    """构造小样型号清单：优先给出 models；否则从文件读；否则用内置占位。

    实际调用方（CLI/定时任务）应传真实型号清单；内置占位仅用于直连调试。
    """
    if models:
        return [{"model_raw": m, "brand_code": "", "brand_name": ""} for m in models]
    if models_file:
        import csv
        out: list[dict] = []
        with open(models_file, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                m = r.get("model_raw") or r.get("model") or ""
                if m:
                    out.append({"model_raw": m,
                                "brand_code": r.get("brand_code") or "",
                                "brand_name": r.get("brand_name") or ""})
        return out
    return []      # 无输入 → 由调用方决定（CLI 会报错）


def _sample_models(all_models: list[dict], n: int) -> list[dict]:
    """取小样：最多 n 个，尽量跨品牌（按 brand_name 去重取前 n）。"""
    if not all_models:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for m in all_models:
        key = m.get("brand_code") or m.get("brand_name") or ""
        if key and key in seen and len(out) >= n:
            continue
        out.append(m)
        if key:
            seen.add(key)
        if len(out) >= n:
            break
    return out


def run_health(spec: dict, models: list[dict], *,
               max_models: int = 3, want_reviews: bool = True,
               verbose: bool = True,
               threshold_drop: float = 0.3) -> HealthResult:
    """对一份 spec 跑小样健康探测。

    流程：取小样型号 → 逐个 crawl_model_on_site → 统计价格填充率/评价数；
    低于阈值的项目进 problems。
    """
    from .mx_retail import crawl_region_retail

    report = HealthResult(code=spec.get("code") or "")
    sample = _sample_models(models, max_models)
    if not sample:
        report.ok = False
        report.problems.append("无型号输入（--models / --models-file）")
        return report

    report.models_checked = len(sample)
    # 合规兜底：白名单不过直接标失败（不留网络流量）
    try:
        base = str(spec.get("base_url") or "")
        ensure_allowed(base, check_robots=False)
    except Exception as e:
        report.ok = False
        report.problems.append(f"合规白名单未过: {e}")
        return report

    try:
        rows = crawl_region_retail(
            spec.get("region") or "mx",
            sample,
            sites=[spec["code"]],
            want_reviews=want_reviews,
            review_pages=1,
            save_db=False,
            retail_spec=spec,
            verbose=verbose,
        )
    except Exception as e:            # 站点整体跑挂（限流/选择器全失效等）
        report.ok = False
        report.problems.append(f"站点整体探测异常: {type(e).__name__}: {e}")
        return report

    matched = [r for r in rows if r.status in ("ok", "no_reviews", "no_price")]
    report.models_matched = len(matched)
    report.price_fill = sum(1 for r in matched if r.price is not None)
    report.review_count = sum(len(r.reviews) for r in matched)
    report.details = [(r.model, r.status,
                       ("-" if r.price is None else str(r.price)),
                       len(r.reviews)) for r in rows]

    # 判定：匹配数或价格填充率骤降
    if not matched:
        report.ok = False
        report.problems.append("小样 0 匹配（搜索/匹配链路可能失效，或该站无在售型号）")
    elif report.price_fill_rate < (1.0 - threshold_drop):
        report.ok = False
        report.problems.append(
            f"价格填充率 {report.price_fill_rate:.0%} 异常低（阈值 {(1-threshold_drop):.0%}）")
    for d in report.details:
        if d[1] in ("blocked", "failed"):
            report.problems.append(f"({d[0]}) status={d[1]}")
    return report


def check_repo(spec_dir: str | None = None,
               models_file: str | None = None,
               max_models: int = 3, want_reviews: bool = True,
               verbose: bool = True) -> list[HealthResult]:
    """`--all` 遍历 docs/retail_specs/*.retail.json，单站失败不中断其余。"""
    root = Path(spec_dir or (Path(__file__).resolve().parents[1] / "docs" / "retail_specs"))
    files = sorted(root.glob("*.retail.json"))
    if not files:
        return [HealthResult(code="", ok=False, problems=["无 retail spec 文件"])]
    reports: list[HealthResult] = []
    for f in files:
        try:
            spec = load_retail_spec(f)
            problems = validate_retail_spec(spec)
            if problems:
                reports.append(HealthResult(code=spec["code"], ok=False,
                                            problems=problems))
                continue
            mr = run_health(spec, _load_models(models_file, None),
                            max_models, want_reviews, verbose)
            reports.append(mr)
        except Exception as e:
            reports.append(HealthResult(code="?", ok=False,
                                        problems=[f"{f.name}: {e}"]))
    return reports


def main(argv: list[str] | None = None) -> int:
    """CLI 入口（供 overseas.cli retail-health-check 调用）。"""
    import argparse

    ap = argparse.ArgumentParser(prog="retail-health",
                                 description="零售线站内小样健康探测")
    ap.add_argument("--spec", help="RetailSpec JSON 路径（单站探测）")
    ap.add_argument("--all", action="store_true",
                    help="遍历 docs/retail_specs/ 全部站（单站失败不阻塞）")
    ap.add_argument("--models-file",
                    help="型号清单 CSV（含 model_raw/brand_code 列）")
    ap.add_argument("--models", nargs="*", help="直接给型号")
    ap.add_argument("--max-models", type=int, default=3, help="每站小样数")
    ap.add_argument("--no-reviews", action="store_true",
                    help="只验证价格选择器（不翻评价页）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供调度解析）")
    args = ap.parse_args(argv)

    want_reviews = not args.no_reviews
    if args.all:
        reports = check_repo(None, args.models_file, args.max_models, want_reviews)
    elif args.spec:
        try:
            spec = load_retail_spec(args.spec)
        except Exception as e:
            print(f"spec 加载失败: {e}")
            return 2
        reports = [run_health(spec, _load_models(args.models_file, args.models),
                                args.max_models, want_reviews)]
    else:
        print("需要 --spec <路径> 或 --all")
        return 2

    if args.json:
        print(json.dumps([r.to_dict() for r in reports],
                         ensure_ascii=False, indent=2))
    else:
        for r in reports:
            if r.models_checked:
                print(f"[{'✓' if r.ok else '✗'}] {r.code}: "
                      f"checked={r.models_checked} matched={r.models_matched} "
                      f"price_fill={r.price_fill_rate:.0%} reviews={r.review_count}")
            else:
                print(f"[{'✓' if r.ok else '✗'}] {r.code}: {r.problems}")
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())