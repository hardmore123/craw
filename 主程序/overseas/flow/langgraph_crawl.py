"""LangGraph全自动流程化海外爬取引擎。

用法:
    # 全量5线
    python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia

    # 单线
    python -m overseas.flow.langgraph_crawl --lines japan

    # 断点续跑
    python -m overseas.flow.langgraph_crawl --resume
"""
from __future__ import annotations
import os, sys, json, argparse
sys.stdout.reconfigure(encoding="utf-8")

from langgraph.graph import StateGraph, END
from .state import CrawlState
from .nodes.init import init_node
from .nodes.spec_crawl import spec_node
from .nodes.retail_monitor import retail_node
from .nodes.review_incremental import review_node
from .nodes.verify import verify_node
from .nodes.report import report_node
from .nodes.retry import retry_node
from .edges import after_spec, after_retail, after_retry, after_review, after_verify

CHECKPOINT_FILE = "data/crawl_state.json"

LINE_NAMES = {
    "japan": "日本", "na": "北美", "sa": "南美",
    "eu": "欧洲", "asia": "亚洲",
}


def _save_checkpoint(state: CrawlState):
    """持久化State到JSON，支持断点续跑。"""
    os.makedirs("data", exist_ok=True)
    # 只保存可序列化字段
    saveable = {k: v for k, v in state.items()
                if k not in ("db",) and not k.startswith("_")}
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(saveable, f, ensure_ascii=False, default=str, indent=2)


def _load_checkpoint() -> CrawlState | None:
    """从JSON恢复State。"""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def build_graph():
    """构建LangGraph状态图。"""
    graph = StateGraph(CrawlState)

    # 注册节点
    graph.add_node("init", init_node)
    graph.add_node("spec", spec_node)
    graph.add_node("retail", retail_node)
    graph.add_node("review", review_node)
    graph.add_node("verify", verify_node)
    graph.add_node("report", report_node)
    graph.add_node("retry", retry_node)

    # 设置入口
    graph.set_entry_point("init")

    # 条件边
    graph.add_conditional_edges("init", lambda s: "spec" if s.get("stage") != "disk_full" else END)
    graph.add_conditional_edges("spec", after_spec, {"retail": "retail", "verify": "verify"})
    graph.add_conditional_edges("retail", after_retail, {"retry": "retry", "review": "review"})
    graph.add_conditional_edges("retry", after_retry, {"review": "review"})
    graph.add_conditional_edges("review", after_review, {"verify": "verify"})
    graph.add_conditional_edges("verify", after_verify, {"report": "report"})
    graph.add_edge("report", END)

    return graph.compile()


def run_line(line: str, db_path: str, proxy: str) -> CrawlState:
    """执行单线流程。"""
    print(f"\n{'='*80}")
    print(f"  LangGraph流程开始: {LINE_NAMES.get(line, line)}线")
    print(f"{'='*80}")

    state: CrawlState = {
        "lines": [line],
        "current_line": line,
        "db_path": db_path,
        "proxy": proxy,
        "stage": "init",
    }

    graph = build_graph()
    final_state = graph.invoke(state, config={"recursion_limit": 100})

    _save_checkpoint(final_state)
    return final_state


def run_all(lines: list[str], db_path: str, proxy: str):
    """执行多线流程（串行）。"""
    all_results = {}
    for i, line in enumerate(lines):
        print(f"\n{'#'*80}")
        print(f"  线路 {i+1}/{len(lines)}: {LINE_NAMES.get(line, line)}线")
        print(f"{'#'*80}")

        state = run_line(line, db_path, proxy)
        all_results[line] = state

        # 增量统计
        delta_sr = state.get("spec_row_after", 0) - state.get("spec_row_before", 0)
        delta_pr = state.get("price_after", 0) - state.get("price_before", 0)
        delta_rv = state.get("review_after", 0) - state.get("review_before", 0)
        print(f"\n  [{LINE_NAMES.get(line, line)}线] spec_row=+{delta_sr} "
              f"price=+{delta_pr} review=+{delta_rv}")

    # 全局汇总
    print(f"\n{'='*80}")
    print(f"  全部{len(lines)}线流程完成")
    print(f"{'='*80}")
    total_sr = sum(s.get("spec_row_after", 0) - s.get("spec_row_before", 0)
                   for s in all_results.values())
    total_pr = sum(s.get("price_after", 0) - s.get("price_before", 0)
                   for s in all_results.values())
    total_rv = sum(s.get("review_after", 0) - s.get("review_before", 0)
                   for s in all_results.values())
    print(f"  全局增量: spec_row=+{total_sr} price=+{total_pr} review=+{total_rv}")
    return all_results


def main():
    parser = argparse.ArgumentParser(description="LangGraph全自动海外爬取流程")
    parser.add_argument("--lines", default="japan,na,sa,eu,asia",
                        help="线路，逗号分隔(japan,na,sa,eu,asia)")
    parser.add_argument("--db", default="data/overseas.db", help="数据库路径")
    parser.add_argument("--proxy", default="http://127.0.0.1:7877", help="代理地址")
    parser.add_argument("--resume", action="store_true", help="断点续跑")
    args = parser.parse_args()

    lines = [l.strip() for l in args.lines.split(",") if l.strip()]

    # 设置环境
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("OVERSEAS_PROXY", args.proxy)
    os.environ.setdefault("OVERSEAS_HEADLESS", "true")
    os.environ.setdefault("OVERSEAS_USE_PROFILE", "1")

    if args.resume:
        # 断点续跑：加载checkpoint，跳过已完成的线
        checkpoint = _load_checkpoint()
        if checkpoint:
            completed_lines = [l for l in lines
                             if checkpoint.get("current_line") == l
                             and checkpoint.get("stage") == "done"]
            remaining = [l for l in lines if l not in completed_lines]
            if remaining:
                print(f"[RESUME] 跳过已完成线，继续: {remaining}")
                lines = remaining
            else:
                print("[RESUME] 全部线已完成")
                return

    run_all(lines, args.db, args.proxy)


if __name__ == "__main__":
    main()
