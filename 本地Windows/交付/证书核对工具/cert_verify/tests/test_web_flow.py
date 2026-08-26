"""Web 全链路集成测试（离线）：upload → run → progress → download。

用 monkeypatch 把 ProviderRouter.query 换成预置返回，避免真实联网。
断言：进度单调、new_rows 无重叠、源文件未改、下载文件存在且不一致已标红；
另测 upload 类型校验与 download 路径安全。
"""
import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from http.server import ThreadingHTTPServer  # noqa: E402

from cert_verify import providers as prov  # noqa: E402
from cert_verify.models import FOUND, NOT_FOUND, CertRecord  # noqa: E402
from cert_verify.web import server as websrv  # noqa: E402

_fail = 0


def check(name, cond):
    global _fail
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _fail += 1


# ---- monkeypatch：按证书号返回预置记录 ----
def fake_query(self, cert_no, agency_hint=""):
    if "999" in cert_no:
        return CertRecord(query_no=cert_no, source="VDE", status=FOUND,
                          manufacturer="Xiamen Wanming", model="A24-065N3A",
                          cert_status="Valid Certificate",
                          source_url="https://vde.com/x")
    if "404" in cert_no:
        return CertRecord(query_no=cert_no, source="VDE", status=NOT_FOUND,
                          message="官网未查到")
    return CertRecord(query_no=cert_no, source="VDE", status=FOUND,
                      manufacturer="Xiamen Wanming", model="WMR10D112K *",
                      cert_status="Valid Certificate", source_url="https://vde.com/y")


prov.ProviderRouter.query = fake_query


def build_xlsx(path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "S1"
    ws["E1"] = "型号"; ws["G1"] = "制造商"; ws["I1"] = "证书号"
    # 行2：型号不一致（表格 A24-999 vs 官网 A24-065N3A）→ 标红
    ws["E2"] = "A24-000ZZZ"; ws["G2"] = "Xiamen Wanming"; ws["I2"] = "VDE 40000999"
    # 行3：通配一致
    ws["E3"] = "WMR10D112K35"; ws["G3"] = "Xiamen Wanming"; ws["I3"] = "VDE 40000001"
    # 行4：官网未查到
    ws["E4"] = "X"; ws["G4"] = "Y"; ws["I4"] = "VDE 40000404"
    wb.save(path)


def _sha1(p):
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def post_multipart(url, filepath):
    boundary = "----cvtest12345"
    with open(filepath, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(filepath)}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    return json.loads(urllib.request.urlopen(req).read().decode())


def post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    return json.loads(urllib.request.urlopen(req).read().decode())


def get_json(url):
    return json.loads(urllib.request.urlopen(url).read().decode())


# ---- 起服务 ----
httpd = ThreadingHTTPServer(("127.0.0.1", 0), websrv.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

import tempfile  # noqa: E402
tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "clean.xlsx")
build_xlsx(src)
src_hash = _sha1(src)

print("=== upload 类型校验 ===")
try:
    bad = os.path.join(tmp, "x.txt")
    open(bad, "w").write("hi")
    r = post_multipart(BASE + "/api/upload", bad)
    check("非 xlsx 被拒", "error" in r)
except Exception as e:
    check("非 xlsx 被拒(异常也算拒绝)", True)

print("=== upload ===")
up = post_multipart(BASE + "/api/upload", src)
check("上传返回 file_id", bool(up.get("file_id")))
check("解析出 1 个 sheet", len(up.get("sheets", [])) == 1)

print("=== run ===")
run = post_json(BASE + "/api/run", {
    "file_id": up["file_id"],
    "sheets": [{"name": "S1", "enabled": True,
                "colmap": {"cert_no": "I", "result": "M", "manufacturer": "G",
                           "model": "E", "agency": "I", "first_data_row": "2"}}],
    "limit": 0, "detail_col": "N", "stop_on_blocked": False, "row_delay": 0,
})
task_id = run["task_id"]
check("run 返回 task_id", bool(task_id))

print("=== progress 轮询 ===")
offset = 0
seen = []
last_done = 0
mono = True
for _ in range(60):
    d = get_json(f"{BASE}/api/progress?id={task_id}&offset={offset}")
    if d["done"] < last_done:
        mono = False
    last_done = d["done"]
    for r in d["new_rows"]:
        seen.append(r)
    offset = d["next_offset"]
    if d["status"] in ("completed", "stopped", "failed"):
        final = d
        break
    time.sleep(0.2)
check("进度单调不减", mono)
check("收齐 3 行且无重叠", len(seen) == 3)
check("状态 completed", final["status"] == "completed")
check("统计：查到2 未查到1", final["stats"]["found"] == 2 and final["stats"]["not_found"] == 1)

# 行2应不一致
r2 = next(r for r in seen if r["row"] == 2)
check("行2 判不一致", r2["consistent"] is False)
mfield = next(f for f in r2["fields"] if f["field"] == "model")
check("行2 型号 MISMATCH", mfield["verdict"] == "不一致")

print("=== download 对比表格 + 源文件不变 ===")
check("源文件字节未改", _sha1(src) == src_hash)
data = urllib.request.urlopen(f"{BASE}/api/download?id={task_id}&kind=diff").read()
check("下载到 xlsx 内容", data[:2] == b"PK")
out = os.path.join(tmp, "dl.xlsx")
open(out, "wb").write(data)
from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(out); ws = wb["S1"]
check("行2 型号单元格标红", ws["E2"].font.color and ws["E2"].font.color.rgb == "FFFF0000")
check("行3 型号未标红", not (ws["E3"].font.color and ws["E3"].font.color.rgb == "FFFF0000"))
check("行2 M列写结论(不一致)", ws["M2"].value and "不一致" in ws["M2"].value)
check("对比表格保留原型号值", ws["E2"].value == "A24-000ZZZ")
wb.close()

print("=== download 抓取表格（官网值）===")
data2 = urllib.request.urlopen(f"{BASE}/api/download?id={task_id}&kind=crawl").read()
out2 = os.path.join(tmp, "dl_crawl.xlsx")
open(out2, "wb").write(data2)
wb2 = load_workbook(out2); ws2 = wb2["S1"]
check("抓取表格 行2 型号=官网值", ws2["E2"].value == "A24-065N3A")
check("抓取表格 M列=查询状态", ws2["M2"].value and "查到" in ws2["M2"].value)
wb2.close()

print("=== download 路径安全 ===")
try:
    urllib.request.urlopen(f"{BASE}/api/download?id=../../etc")
    check("非法 id 被拒", False)
except urllib.error.HTTPError as e:
    check("非法 id 返回错误码", e.code in (400, 404))

httpd.shutdown()
print("\n" + ("全部通过" if _fail == 0 else f"{_fail} 项失败"))
sys.exit(1 if _fail else 0)
