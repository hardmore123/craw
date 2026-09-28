"""report_node — 生成增量报告markdown。

输出到 海外/<线名>线LangGraph增量报告.md
"""
from __future__ import annotations
import os, json, datetime
from ..state import CrawlState

LINE_NAMES = {
    "japan": "日本", "na": "北美", "sa": "南美",
    "eu": "欧洲", "asia": "亚洲",
}


def report_node(state: CrawlState) -> CrawlState:
    """报告节点：生成markdown报告。"""
    line = state.get("current_line", "")
    line_name = LINE_NAMES.get(line, line)

    spec_results = state.get("spec_results", {})
    retail_results = state.get("retail_results", {})
    review_results = state.get("review_results", {})

    delta_sr = state.get("spec_row_after", 0) - state.get("spec_row_before", 0)
    delta_pr = state.get("price_after", 0) - state.get("price_before", 0)
    delta_rv = state.get("review_after", 0) - state.get("review_before", 0)

    # SPEC结果汇总
    spec_ok = sum(1 for r in spec_results.values() if r.get("ok", 0) > 0)
    spec_fail = sum(1 for r in spec_results.values() if r.get("fail", 0) > 0 and r.get("ok", 0) == 0)

    # RETAIL结果汇总
    retail_ok = sum(1 for r in retail_results.values() if "ok=" in r.get("output", "") and "ok=0" not in r.get("output", ""))
    retail_timeout = sum(1 for r in retail_results.values() if r.get("timeout"))
    retail_blocked = len(state.get("blocked_sites", []))

    report = f"""# {line_name}线LangGraph增量抓取报告

> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} | LangGraph自动流程 | 周={state.get('week', '')}

## 流程执行结果

| 阶段 | 站数 | 成功 | 失败 | 超时 | 封锁 |
|------|------|------|------|------|------|
| SPEC | {len(spec_results)} | {spec_ok} | {spec_fail} | — | — |
| RETAIL | {len(retail_results)} | {retail_ok} | — | {retail_timeout} | {retail_blocked} |
| REVIEW | {len(review_results)} | — | — | — | — |

## 增量数据

| 指标 | 抓取前 | 抓取后 | 增量 |
|------|--------|--------|------|
| spec_row | {state.get('spec_row_before', 0):,} | {state.get('spec_row_after', 0):,} | +{delta_sr:,} |
| 价格快照 | {state.get('price_before', 0):,} | {state.get('price_after', 0):,} | +{delta_pr:,} |
| 网评 | {state.get('review_before', 0):,} | {state.get('review_after', 0):,} | +{delta_rv:,} |

## SPEC结果

| 站点 | ok | fail | blocked |
|------|-----|------|---------|
"""
    for code, r in spec_results.items():
        report += f"| {code} | {r.get('ok', 0)} | {r.get('fail', 0)} | {r.get('blocked', 0)} |\n"

    report += f"""
## RETAIL结果

| 站点 | 结果 |
|------|------|
"""
    for code, r in retail_results.items():
        report += f"| {code} | {r.get('output', 'N/A')[:60]} |\n"

    if state.get("timeout_sites"):
        report += f"\n## 超时站({len(state['timeout_sites'])}站)\n\n"
        for s in state["timeout_sites"]:
            report += f"- {s}\n"

    if state.get("blocked_sites"):
        report += f"\n## WAF封锁站({len(state['blocked_sites'])}站)\n\n"
        for s in state["blocked_sites"]:
            report += f"- {s}\n"

    if state.get("errors"):
        report += f"\n## 警告\n\n"
        for e in state["errors"]:
            report += f"- {e}\n"

    # 写报告
    out_dir = os.path.join(os.getcwd(), "..", "海外")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{line_name}线LangGraph增量报告.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n[REPORT] 报告已生成: {out_path}")
    state["stage"] = "done"
    state["report_path"] = out_path
    return state
