"""列映射与运行参数配置。

拿到真实表格后，先跑：
    python -m cert_verify.cli inspect "电源电路部分安全件报备清单明细表_认证类别UL-CB 20230420.xlsx"
把打印出来的列字母填到下面 DEFAULT_COLUMNS / SHEET_COLUMNS 里。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ColumnMap:
    """一个 sheet 的列映射（用 Excel 列字母，空字符串表示该列不存在）。"""

    cert_no: str = "I"            # 证书号 —— 题目已明确
    result: str = "M"             # 比对结果 —— 题目已明确

    manufacturer: str = ""        # 制造商
    factory: str = ""             # 生产厂
    model: str = ""               # 型号
    standard: str = ""            # 标准
    cert_status: str = ""         # 证书状态
    agency: str = ""              # 认证机构（VDE/UL/TUV），没有就靠 default_agency

    first_data_row: int = 2       # 数据起始行（表头占几行按实际改）


# 全局默认
DEFAULT_COLUMNS = ColumnMap()

# 两个 sheet 若列结构不同，在这里分别覆盖，键是 sheet 名
SHEET_COLUMNS: dict[str, ColumnMap] = {
    # "UL": ColumnMap(cert_no="I", result="M", manufacturer="C", factory="D",
    #                 model="E", standard="F", cert_status="J", first_data_row=3),
    # "CB": ColumnMap(...),
}


@dataclass
class RunConfig:
    xlsx_path: str
    output_path: str = ""
    sheets: list[str] = field(default_factory=list)   # 空 = 全部 sheet
    limit: int = 0                                     # >0 时只跑前 N 条，先小样验证
    row_delay: float = 0.0                             # 每行之间额外等待（间隔已由 query_interval 控制）
    query_interval: float = 1.0                        # 每条查询固定间隔（秒），网页可自主设置，默认 1s
    query_jitter: float = 1.0                          # 间隔随机抖动上限（秒），默认 1s；0=固定间隔
    headless: bool = True
    default_agency: str = ""                           # 表里没有机构列时，指定如 "TUV"
    write_detail_col: str = ""                         # 可选：把来源 URL 写到某列，如 "N"
    stop_on_blocked: bool = True                       # 一旦被防火墙拦截就停，别写满错误
    columns: ColumnMap | None = None                   # 代码内直接指定列映射（优先于 config 常量）
    proxy: str = ""                                    # 代理；留空则自动探测系统代理（开 VPN 后即用）

    def __post_init__(self):
        if not self.output_path:
            if self.xlsx_path.lower().endswith(".xlsx"):
                self.output_path = self.xlsx_path[:-5] + "_核对结果.xlsx"
            else:
                self.output_path = self.xlsx_path + "_核对结果.xlsx"

    def column_map_for(self, sheet_name: str) -> ColumnMap:
        if self.columns is not None:
            return self.columns
        return SHEET_COLUMNS.get(sheet_name, DEFAULT_COLUMNS)
