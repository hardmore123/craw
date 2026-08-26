"""端到端自测：造一个和描述结构一致的两 sheet 表格（I 列证书号、M 列结果），
跑完整流程，验证抓取 + 比对 + 写回。

用的都是 Certipedia 上真实存在的 TUV 证书号，其中故意埋了不一致和不存在的行。
    python -m cert_verify.selftest
"""
from __future__ import annotations

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "_selftest", "样例清单.xlsx")

HEADER = ["序号", "部件名称", "制造商", "生产厂", "型号", "标准",
          "认证机构", "证书类别", "证书号", "证书状态", "备注1", "备注2", "核对结果"]
#           A       B          C        D        E       F        G         H        I(9)     J(10)     K       L       M(13)

ROWS = [
    # 全字段应一致
    [1, "电源适配器", "Chicony Power Technology Co., Ltd.", "", "A24-065N3A",
     "EN IEC 62368-1:2020+A11", "TUV", "GS", "50679826", "有效", "", "", ""],
    # 型号故意写错 -> 应报不一致
    [2, "电源适配器", "Chicony Power Technology Co., Ltd.", "", "A24-999XXX",
     "EN IEC 62368-1:2020+A11", "TUV", "GS", "50679826", "有效", "", "", ""],
    # 制造商故意写错 -> 应报不一致
    [3, "鼠标", "Some Other Vendor Inc.", "", "(refer to accompanying certificates)",
     "EN 62368-1:2014", "TUV", "GS", "50543817", "有效", "", "", ""],
    # 标准只差年份 -> 应判一致并提示版本不同
    [4, "电源适配器", "Chicony Power Technology", "", "A24-065N3A",
     "EN 62368-1:2014", "TUV", "GS", "50679826", "有效", "", "", ""],
    # 官网查不到
    [5, "未知件", "Nobody Ltd.", "", "X-1", "EN 60950-1", "TUV", "GS", "99999999", "有效", "", "", ""],
    # 空证书号，应跳过
    [6, "跳过行", "", "", "", "", "", "", "", "", "", "", ""],
]

VDE_ROWS = [
    # VDE 一行，用于验证"被网络拦截"的处理路径
    [1, "变压器", "Some Trafo GmbH", "", "TR-100", "EN 61558-2-16", "VDE", "VDE", "40040940",
     "有效", "", "", ""],
]


def build_fixture() -> str:
    from openpyxl import Workbook
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "UL-CB"
    ws.append(HEADER)
    for r in ROWS:
        ws.append(r)
    ws2 = wb.create_sheet("VDE")
    ws2.append(HEADER)
    for r in VDE_ROWS:
        ws2.append(r)
    wb.save(FIXTURE)
    return FIXTURE


def main():
    from .config import ColumnMap, RunConfig
    from .excel_runner import run, summarize

    path = build_fixture()
    print(f"已生成样例表: {path}\n")

    cmap = ColumnMap(cert_no="I", result="M", manufacturer="C", factory="D",
                     model="E", standard="F", cert_status="J", agency="G",
                     first_data_row=2)

    # 只跑 TUV 那个 sheet：VDE 目前被网络拦截，单独跑会直接 stop
    cfg = RunConfig(xlsx_path=path, sheets=["UL-CB"], columns=cmap,
                    row_delay=0.2, write_detail_col="N")
    res = run(cfg)
    print("\n" + summarize(res))

    lines = ["--- 逐条结果 ---"]
    for r in res:
        lines.append(f"[{r.sheet} r{r.row}] {r.cert_no} -> {r.verdict}")
    lines.append("")
    lines.append(summarize(res))
    report = os.path.join(HERE, "_selftest", "report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n报告: {report}")


if __name__ == "__main__":
    main()
