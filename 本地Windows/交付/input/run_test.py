"""对报备清单做小样测试：认证类别UL / 认证类别CB 两个 sheet，各跑前几条。

列映射（两 sheet 相同）：
  I=证书号（带机构前缀 'UL E67006' / 'VDE 40018250'，用于路由 + 去前缀查询）
  G=制造商  E=型号  H=标准   结果写 M
  证书状态/生产厂：表格无此列，留空（比对时按"表格未填"处理，网站值仍会显示）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cert_verify.config import ColumnMap, RunConfig  # noqa: E402
from cert_verify.excel_runner import run, summarize  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
XLSX = os.path.join(HERE, "电源电路部分安全件报备清单明细表_认证类别UL-CB 20230420 (1).xlsx")

cmap = ColumnMap(
    cert_no="I", result="M",
    manufacturer="G", model="E", standard="H",
    cert_status="", factory="",
    agency="J",              # J 列 = 认证类别（CB→VDE，UL/CB/UL→UL），作为路由依据
    first_data_row=2,
)

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
sheets = sys.argv[2].split(",") if len(sys.argv) > 2 else ["认证类别CB", "认证类别UL"]
OUT = os.path.join(HERE, "核对结果_测试.xlsx")
PROXY = os.environ.get("CERT_PROXY", "http://127.0.0.1:7877")

# 链式：每个 sheet 单独跑，后一个读上一个的输出，结果都累积到同一文件
src = XLSX
all_res = []
for sh in sheets:
    cfg = RunConfig(
        xlsx_path=src, output_path=OUT, sheets=[sh], columns=cmap,
        limit=limit, write_detail_col="N", row_delay=0.3,
        stop_on_blocked=False, proxy=PROXY,
    )
    print(f"\n########## 测试 sheet={sh} limit={limit} ##########")
    all_res += run(cfg)
    src = OUT      # 下一个 sheet 基于已写入的结果继续

print("\n" + summarize(all_res))
