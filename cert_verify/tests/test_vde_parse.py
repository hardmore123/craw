"""VDE 解析器离线测试 —— 用真实接口响应做基准，不需要联网。

vde.com 在本机被网关拦截，但接口响应是真的（从浏览器 DevTools 抓的
证书号 40052271 的完整 JSON，存在 fixtures/ 下），所以解析逻辑可以
100% 对着真实数据验证，网络放通后无需再改。

    python cert_verify\\tests\\test_vde_parse.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cert_verify.compare import cmp_model, compare_record, model_globs, split_models  # noqa: E402
from cert_verify.models import FOUND, NOT_FOUND, CertRecord  # noqa: E402
from cert_verify.providers.vde import VdeProvider  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "vde_40052271.json")

lines: list[str] = []
fails: list[str] = []


def log(m=""):
    print(m)
    lines.append(str(m))


def check(name: str, got, want):
    ok = got == want
    log(f"  [{'OK ' if ok else 'FAIL'}] {name}")
    if not ok:
        log(f"         期望: {want!r}")
        log(f"         实际: {got!r}")
        fails.append(name)


def check_true(name: str, cond, extra=""):
    log(f"  [{'OK ' if cond else 'FAIL'}] {name}{('  ' + str(extra)) if extra else ''}")
    if not cond:
        fails.append(name)


# ============ 1. URL 构造 ============
log("=== 1. 接口 URL 构造 ===")
url = VdeProvider.api_url("40052271", all_fields=False)
log(f"  {url}")
check_true("命中 restSearch 接口", "servlet/restSearch/2289904/zertreg-certificates" in url)
check_true("id 过滤条件正确编码",
           "fq=id%3Azertreg%7Ccertificate%2340052271" in url, )
check_true("detailDocType 过滤存在",
           "fq=%28detailDocType%3Azertreg%7Ccertificate%29" in url)
check_true("wt=json", "wt=json" in url)

hdr = VdeProvider._headers("40052271")
check_true("Referer 指向搜索页", "certificateNumber=40052271" in hdr["Referer"])
check_true("Accept 为 json", "application/json" in hdr["Accept"])

log("\n  HA/NA 前缀去字母（VDE 官方要求）:")
for n in ("40052271", "HA012345", "NA987654", "40040940-4880-0001"):
    log(f"    {n} -> {VdeProvider._normalize(n)}")
check("HA012345 归一化", VdeProvider._normalize("HA012345"), "012345")

# ============ 2. 解析真实响应 ============
log("\n=== 2. 解析真实接口响应（证书号 40052271）===")
with open(FIXTURE, "r", encoding="utf-8") as f:
    data = json.load(f)

rec = VdeProvider.parse(data, CertRecord(query_no="40052271", source="VDE"))
check("查询状态", rec.status, FOUND)
check("制造商", rec.manufacturer, "Xiamen Wanming")
check("证书号", rec.cert_no_on_site, "40052271")
check("证书状态", rec.cert_status, "Valid Certificate")
check("产品类别", rec.cert_type, "Varistor")
check_true("型号取的是完整 typeNames 而不是截断的 firstTypes",
           rec.model.count(";") == 9 and ".." not in rec.model)
log(f"    型号 = {rec.model}")
check("注册号进了 raw", rec.raw_fields.get("__注册号 Reg.-Nr.__"), "E809")
check_true("详情页 URL 正确",
           rec.source_url.endswith("/certificate?id=40052271&type=zertreg%7Ccertificate"),
           rec.source_url)
log(f"    标准 = {rec.standard!r}（接口默认 fl 不含标准，为空属预期）")
log(f"    生产厂 = {rec.factory!r}（同上）")

# ============ 3. 查不到的情况 ============
log("\n=== 3. 查不到时不猜原因 ===")
empty = VdeProvider.parse({"response": {"numFound": 0, "docs": []}},
                          CertRecord(query_no="99999999", source="VDE"))
check("状态为 not_found", empty.status, NOT_FOUND)
check_true("说明里点出可能是已失效", "失效" in empty.message, empty.message)

# ============ 4. 通配型号匹配（VDE 特有）============
log("\n=== 4. 通配型号匹配 ===")
site = rec.model
log(f"  网站型号列表含 * 通配，编译出 {len(model_globs(site))} 个模式")
cases = [
    ("WMR10D112K", "一致", "表格填的是列表里的具体型号"),
    ("WMR10D112K35", "一致", "带后缀，靠 * 通配命中"),
    ("WMR05D180M", "一致", "带 bis/to 的也能命中"),
    ("WMR99D999X", "不一致", "确实不在列表里"),
]
for sheet_model, want, why in cases:
    verdict, detail = cmp_model(sheet_model, site)
    ok = verdict == want
    log(f"  [{'OK ' if ok else 'FAIL'}] {sheet_model:<14} -> {verdict}  ({why})")
    if detail:
        log(f"         {detail[:100]}")
    if not ok:
        fails.append(f"cmp_model({sheet_model})")

check_true("bis/to 被当噪音剔除",
           "bisto" not in "".join(split_models("WMR05D180M * bis/to")))

# ============ 5. 端到端比对 ============
log("\n=== 5. 端到端比对（模拟表格一行）===")
sheet_row = {
    "manufacturer": "厦门万明 Xiamen Wanming Electronic Co., Ltd.",
    "factory": "厦门",
    "model": "WMR10D112K",
    "standard": "EN 61051-1",
    "cert_status": "有效",
}
ok, verdict = compare_record(sheet_row, rec)
log(f"  结论: {verdict}")
check_true("制造商能对上（表格带中文前缀+公司后缀）", "制造商" in verdict and "制造商不一致" not in verdict)
check_true("型号判为一致", "型号不一致" not in verdict)
check_true("标准如实记为网站无此项", "标准网站无此项" in verdict)

# ============ 汇总 ============
log("\n" + "=" * 60)
if fails:
    log(f"失败 {len(fails)} 项: {fails}")
else:
    log("全部通过")

with open(os.path.join(HERE, "parse_report.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

sys.exit(1 if fails else 0)
