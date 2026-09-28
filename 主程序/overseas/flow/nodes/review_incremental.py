"""review_node — 网评增量节点，调用s4_review_incremental。

边界条件：
- 已有评价无新增 → 跳过
- 最多取前50个SKU做网评增量（防超时）
"""
from __future__ import annotations
import os, sys, sqlite3, subprocess
from typing import Any
from ..state import CrawlState

PY = r"C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"
REVIEW_SKU_LIMIT = 20  # 每站最多20个SKU做网评增量


def review_node(state: CrawlState) -> CrawlState:
    """网评增量节点：对有网评的站点做s4_review_incremental。"""
    db_path = state.get("db_path", "data/overseas.db")
    retail_sites = state.get("retail_sites_with_data", [])
    results = state.get("review_results", {})

    if not retail_sites:
        state["stage"] = "verify"
        return state

    # 取有网评的站点（已有review > 0的站优先）
    con = sqlite3.connect(db_path)
    sites_with_reviews = []
    for code in retail_sites:
        r = con.execute("SELECT id FROM site WHERE code=?", (code,)).fetchone()
        if not r:
            continue
        sid = r[0]
        rv = con.execute("SELECT COUNT(*) FROM review r JOIN product p ON r.product_id=p.id WHERE p.site_id=?", (sid,)).fetchone()[0]
        if rv > 0:
            sites_with_reviews.append(code)
    con.close()

    if not sites_with_reviews:
        print("[REVIEW] 无有网评站，跳过")
        state["stage"] = "verify"
        return state

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("OVERSEAS_PROXY", state.get("proxy", "http://127.0.0.1:7877"))
    os.environ.setdefault("OVERSEAS_HEADLESS", "true")

    total_new = 0
    for code in sites_with_reviews[:10]:  # 最多10站
        print(f"\n[REVIEW] {code}")
        try:
            con = sqlite3.connect(db_path)
            sid = con.execute("SELECT id FROM site WHERE code=?", (code,)).fetchone()[0]
            skus = [r[0] for r in con.execute(
                "SELECT sku FROM product WHERE site_id=? LIMIT ?", (sid, REVIEW_SKU_LIMIT)
            ).fetchall()]
            con.close()

            if not skus:
                continue

            script = f"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from overseas.db import Database
from overseas.scenarios import s4_review_incremental
db = Database('{db_path}')
db.init_schema()
try:
    skus = {skus[:5]}
    st = s4_review_incremental('{code}', skus, pages=3, db=db)
    print(f'Result: ok={{st.ok}} reviews_new={{st.reviews_new}}')
except Exception as e:
    print(f'ERROR: {{type(e).__name__}}: {{str(e)[:80]}}')
db.close()
"""
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            r = subprocess.run(
                [PY, "-c", script],
                capture_output=True, text=True,
                timeout=120, encoding="utf-8", env=env, cwd=os.getcwd()
            )
            out = (r.stdout or "").strip()
            for l in out.split("\n")[-3:]:
                print(f"  {l}")
            results[code] = {"output": out[-200:] if out else "no output"}
            if "reviews_new=" in out:
                try:
                    rn = int(out.split("reviews_new=")[1].split()[0].strip("}"))
                    total_new += rn
                except:
                    pass
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT (120s)")
            results[code] = {"output": "TIMEOUT"}
        except Exception as e:
            print(f"  ERROR: {str(e)[:60]}")
            results[code] = {"output": f"ERROR: {str(e)[:60]}"}

    state["review_results"] = results
    state["stage"] = "verify"
    print(f"\n[REVIEW] 完成: reviews_new={total_new}")
    return state
