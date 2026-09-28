"""edges — 条件路由逻辑。"""
from __future__ import annotations
from .state import CrawlState


def after_spec(state: CrawlState) -> str:
    """SPEC完成后路由：有RETAIL站→retail，无→verify。"""
    if state.get("retail_sites_with_data"):
        return "retail"
    return "verify"


def after_retail(state: CrawlState) -> str:
    """RETAIL完成后路由：有失败站→retry，无→review。"""
    if state.get("failed_sites") and any(
        state.get("retry_count", {}).get(s, 0) < state.get("max_retries", 2)
        for s in state.get("failed_sites", [])
    ):
        return "retry"
    return "review"


def after_retry(state: CrawlState) -> str:
    """重试完成后路由：→review。"""
    return "review"


def after_review(state: CrawlState) -> str:
    """网评完成后路由：→verify。"""
    return "verify"


def after_verify(state: CrawlState) -> str:
    """验证完成后路由：→report。"""
    return "report"
