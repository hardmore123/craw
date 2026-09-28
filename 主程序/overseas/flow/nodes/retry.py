"""retry_node — 失败站重试节点。

边界条件：
- 最多重试2次
- 重试时limit减半
- 重试后仍失败 → 标记blocked
"""
from __future__ import annotations
import os
from ..state import CrawlState


def retry_node(state: CrawlState) -> CrawlState:
    """重试节点：对失败站重新运行RETAIL监控（limit减半）。"""
    failed = state.get("failed_sites", [])
    max_retries = state.get("max_retries", 2)
    retry_count = state.get("retry_count", {})

    # 过滤可重试站
    retryable = [s for s in failed if retry_count.get(s, 0) < max_retries]
    if not retryable:
        print("[RETRY] 无可重试站")
        state["stage"] = "report"
        return state

    print(f"[RETRY] 重试 {len(retryable)} 站")
    from .retail_monitor import _run_monitor

    db_path = state.get("db_path", "data/overseas.db")
    timeout = state.get("per_site_timeout", 90)

    for code in retryable:
        retries = retry_count.get(code, 0)
        limit = max(10 // (retries + 1), 3)  # 递减limit
        print(f"\n[RETRY] {code} (第{retries+1}次, limit={limit})")
        res = _run_monitor(code, limit, timeout, db_path)
        state.setdefault("retail_results", {})[code] = res

        if res.get("timeout"):
            state["timeout_sites"] = state.get("timeout_sites", []) + [code]
        elif "ok=" in res.get("output", "") and "ok=0" not in res["output"]:
            print(f"  ✅ 重试成功")
            if code in state.get("failed_sites", []):
                state["failed_sites"].remove(code)
        else:
            retry_count[code] = retries + 1
            if retries + 1 >= max_retries:
                state["blocked_sites"] = state.get("blocked_sites", []) + [code]
                print(f"  ❌ 重试失败，标记blocked")

    state["retry_count"] = retry_count
    state["stage"] = "report"
    return state
