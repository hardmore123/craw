"""CrawlState — LangGraph 全局状态定义。"""
from __future__ import annotations
from typing import TypedDict, Any


class CrawlState(TypedDict, total=False):
    # ── 全局配置 ──
    lines: list[str]              # 待执行的线: ["japan","na","sa","eu","asia"]
    current_line: str             # 当前执行线
    db_path: str                  # 数据库路径
    week: str                     # ISO周 (2026-W40)
    proxy: str                    # 代理地址

    # ── 站点列表 ──
    spec_sites: list[str]          # 当前线SPEC站
    retail_sites: list[str]        # 当前线RETAIL站
    retail_sites_with_data: list[str]  # 有产品的RETAIL站

    # ── 结果 ──
    spec_results: dict[str, Any]   # {site_code: {"ok":N,"fail":N,"rows":N}}
    retail_results: dict[str, Any] # {site_code: {"ok":N,"prices":N,"timeout":bool}}
    review_results: dict[str, Any] # {site_code: {"ok":N,"reviews_new":N}}

    # ── 边界控制 ──
    failed_sites: list[str]        # 失败站
    blocked_sites: list[str]       # WAF硬封站
    timeout_sites: list[str]       # 超时站
    retry_count: dict[str, int]    # {site_code: retry_times}
    max_retries: int               # 最大重试次数(默认2)
    per_site_timeout: int          # 每站超时秒(默认90)

    # ── 流程控制 ──
    stage: str                     # "init"|"spec"|"retail"|"review"|"verify"|"report"|"done"
    line_index: int                # 当前线索引
    site_index: int                # 当前站索引
    errors: list[str]              # 错误日志

    # ── 增量统计 ──
    spec_row_before: int           # 抓取前spec_row
    spec_row_after: int            # 抓取后spec_row
    price_before: int              # 抓取前价格数
    price_after: int               # 抓取后价格数
    review_before: int             # 抓取前网评数
    review_after: int              # 抓取后网评数

    # ── 断点续跑 ──
    checkpoint_path: str           # State JSON持久化路径
    resume: bool                   # 是否断点续跑
