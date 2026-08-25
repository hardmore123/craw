"""compare_fields / summarize_fields 单元测试（离线，不联网）。

覆盖：UL 型号空→网站无此项、VDE 通配→一致、标准年份差异→一致+注记、
真实型号不同→不一致、表格空→表格未填，以及 compare_record 回归。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cert_verify.compare import (  # noqa: E402
    MATCH, MISMATCH, MISSING_SHEET, MISSING_SITE,
    compare_fields, compare_record, summarize_fields,
)
from cert_verify.models import FOUND, NOT_FOUND, BLOCKED, CertRecord  # noqa: E402

_fail = 0


def check(name, cond):
    global _fail
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _fail += 1


def field(fvs, name):
    return next(f for f in fvs if f.field == name)


print("=== 1. UL：型号/标准空 → 网站无此项，不判不一致 ===")
rec = CertRecord(query_no="E502081", source="UL", status=FOUND,
                 manufacturer="NANJING TENGEN RONG GUANG DA ELECTRONIC", model="", standard="",
                 cert_status="Listed")
row = {"manufacturer": "Nanjing Tengen Rong Guang Da Electronic Co., Ltd.",
       "model": "382", "standard": "UL 60384", "factory": "", "cert_status": ""}
fvs = compare_fields(row, rec)
check("型号=网站无此项", field(fvs, "model").verdict == MISSING_SITE)
check("标准=网站无此项", field(fvs, "standard").verdict == MISSING_SITE)
consistent, text = summarize_fields(fvs)
check("整行一致（型号未判不一致）", consistent is True)
check("摘要不含✗", "✗" not in text)

print("=== 2. VDE 通配型号 → 一致 ===")
rec = CertRecord(source="VDE", status=FOUND, manufacturer="Xiamen Wanming",
                 model="WMR10D112K *; WMR05D561K *", cert_status="Valid Certificate")
row = {"manufacturer": "Xiamen Wanming", "model": "WMR10D112K35",
       "standard": "", "factory": "", "cert_status": ""}
fvs = compare_fields(row, rec)
check("通配型号判一致", field(fvs, "model").verdict == MATCH)

print("=== 3. 标准仅年份/修订不同 → 一致 + 注记 ===")
rec = CertRecord(source="TUV-Rheinland", status=FOUND,
                 standard="EN IEC 62368-1:2020+A11")
row = {"standard": "EN 62368-1:2014", "manufacturer": "", "model": "",
       "factory": "", "cert_status": ""}
fvs = compare_fields(row, rec)
std = field(fvs, "standard")
check("标准判一致", std.verdict == MATCH)
check("注记含版本/年份", "版本" in std.detail or "年份" in std.detail)

print("=== 4. 真实型号不同 → 不一致，detail 含官网值 ===")
rec = CertRecord(source="TUV-Rheinland", status=FOUND, model="A24-065N3A")
row = {"model": "A24-999XXX", "manufacturer": "", "standard": "",
       "factory": "", "cert_status": ""}
fvs = compare_fields(row, rec)
m = field(fvs, "model")
check("型号判不一致", m.verdict == MISMATCH)
check("site_value 为官网型号", "A24-065N3A" in m.site_value)
consistent, text = summarize_fields(fvs)
check("整行不一致", consistent is False and "✗" in text)
check("摘要含官网值", "A24-065N3A" in text)

print("=== 5. 表格未填 → MISSING_SHEET ===")
rec = CertRecord(source="VDE", status=FOUND, manufacturer="Xiamen Wanming")
row = {"manufacturer": "", "model": "", "standard": "", "factory": "", "cert_status": ""}
fvs = compare_fields(row, rec)
check("制造商=表格未填", field(fvs, "manufacturer").verdict == MISSING_SHEET)

print("=== 6. 非 found 状态 → compare_fields 返回空 ===")
rec = CertRecord(source="VDE", status=NOT_FOUND, message="官网未查到")
check("not_found 返回空列表", compare_fields(row, rec) == [])
consistent, text = compare_record(row, rec)
check("compare_record not_found 文案", consistent is False and "官网未查到" in text)

rec = CertRecord(source="VDE", status=BLOCKED, message="被拦截")
consistent, text = compare_record(row, rec)
check("compare_record blocked 文案", consistent is False and "拦截" in text)

print("\n" + ("全部通过" if _fail == 0 else f"{_fail} 项失败"))
sys.exit(1 if _fail else 0)
