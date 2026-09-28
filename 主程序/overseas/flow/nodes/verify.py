"""verify_node — 验证DB增量数据。

边界条件：
- 增量=0 → 标记警告（不阻塞流程）
"""
from __future__ import annotations
import sqlite3
from ..state import CrawlState


def verify_node(state: CrawlState) -> CrawlState:
    """验证节点：对比抓取前后的DB数据增量。"""
    db_path = state.get("db_path", "data/overseas.db")
    con = sqlite3.connect(db_path)

    sr_after = con.execute("SELECT COUNT(*) FROM spec_row").fetchone()[0]
    pr_after = con.execute("SELECT COUNT(*) FROM price_snapshot").fetchone()[0]
    rv_after = con.execute("SELECT COUNT(*) FROM review").fetchone()[0]

    state["spec_row_after"] = sr_after
    state["price_after"] = pr_after
    state["review_after"] = rv_after

    sr_before = state.get("spec_row_before", 0)
    pr_before = state.get("price_before", 0)
    rv_before = state.get("review_before", 0)

    delta_sr = sr_after - sr_before
    delta_pr = pr_after - pr_before
    delta_rv = rv_after - rv_before

    print(f"\n[VERIFY] spec_row: {sr_before} → {sr_after} (+{delta_sr})")
    print(f"[VERIFY] price: {pr_before} → {pr_after} (+{delta_pr})")
    print(f"[VERIFY] review: {rv_before} → {rv_after} (+{delta_rv})")

    warnings = []
    if delta_sr == 0 and state.get("spec_sites"):
        warnings.append("SPEC增量=0")
    if delta_pr == 0 and state.get("retail_sites_with_data"):
        warnings.append("价格增量=0（可能代理超时）")

    if warnings:
        state["errors"] = state.get("errors", []) + warnings
        print(f"[VERIFY] 警告: {', '.join(warnings)}")

    con.close()
    state["stage"] = "report"
    return state
