"""retail_node — RETAIL价格监控节点，调用s2_monitor_known（并行版）。

边界条件：
- 每站90秒超时 → 标记TIMEOUT
- 连续3 blocked → 跳过该站剩余SKU
- 磁盘满 → 暂停
- 并行度: 默认3站同时执行（可配置parallel_workers）
- 并行时每站独立子进程，避免SQLite线程冲突
"""
from __future__ import annotations
import os, sys, subprocess, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from ..state import CrawlState

PY = r"C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"
RETAIL_LIMIT = 10  # 每站最多监控SKU数(防超时)
DEFAULT_PARALLEL = 3  # 默认并行站数


def _run_monitor(site: str, limit: int, timeout: int, db_path: str) -> dict:
    """在子进程中运行s2_monitor_known，避免SQLite线程问题。"""
    script = f"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from overseas.db import Database
from overseas.scenarios import s2_monitor_known
db = Database('{db_path}')
db.init_schema()
try:
    st = s2_monitor_known('{site}', limit={limit}, db=db, verbose=True)
    print(f'Result: ok={{st.ok}} fail={{st.fail}} blocked={{st.blocked}} products={{st.products}} prices={{st.prices}} reviews_new={{st.reviews_new}}')
except Exception as e:
    print(f'ERROR: {{type(e).__name__}}: {{str(e)[:80]}}')
db.close()
"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("OVERSEAS_PROXY", "http://127.0.0.1:7877")
    env.setdefault("OVERSEAS_HEADLESS", "true")
    env.setdefault("OVERSEAS_USE_PROFILE", "1")

    try:
        r = subprocess.run(
            [PY, "-c", script],
            capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", env=env,
            cwd=os.getcwd(),
        )
        out = (r.stdout or "").strip()
        lines = out.split("\n") if out else []
        result_line = [l for l in lines if "Result:" in l or "ERROR:" in l]
        if result_line:
            return {"output": result_line[-1]}
        return {"output": "no output"}
    except subprocess.TimeoutExpired:
        return {"output": "TIMEOUT", "timeout": True}
    except Exception as e:
        return {"output": f"EXCEPTION: {str(e)[:60]}"}


def _run_one(args: tuple) -> tuple[str, dict]:
    """并行执行单元：返回 (site_code, result)。"""
    site, limit, timeout, db_path = args
    t0 = time.time()
    res = _run_monitor(site, limit, timeout, db_path)
    elapsed = round(time.time() - t0, 1)
    res["elapsed"] = elapsed
    return site, res


def retail_node(state: CrawlState) -> CrawlState:
    """RETAIL价格监控节点：并行遍历有产品的retail站。

    并行策略：
    - 使用ThreadPoolExecutor并行启动子进程
    - 默认3站同时执行（可通过state['parallel_workers']配置）
    - 每站独立子进程+独立DB连接，避免SQLite线程冲突
    - 单站超时不影响其他站
    """
    db_path = state.get("db_path", "data/overseas.db")
    retail_sites = state.get("retail_sites_with_data", [])
    timeout_sec = state.get("per_site_timeout", 90)
    parallel_workers = state.get("parallel_workers", DEFAULT_PARALLEL)
    results = state.get("retail_results", {})

    if not retail_sites:
        print("[RETAIL] 无有数据RETAIL站，跳过")
        state["stage"] = "review"
        return state

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    print(f"[RETAIL] {len(retail_sites)}站，并行度={parallel_workers}，每站超时={timeout_sec}s")

    # 构建任务列表
    tasks = []
    for code in retail_sites:
        retries = state.get("retry_count", {}).get(code, 0)
        limit = RETAIL_LIMIT if retries == 0 else max(RETAIL_LIMIT // 2, 3)
        tasks.append((code, limit, timeout_sec, db_path))

    # 并行执行
    total_ok = total_timeout = total_blocked = 0
    completed = 0
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        future_to_site = {executor.submit(_run_one, task): task[0] for task in tasks}

        for future in as_completed(future_to_site):
            site_code = future_to_site[future]
            completed += 1
            try:
                code, res = future.result()
            except Exception as e:
                res = {"output": f"EXCEPTION: {str(e)[:60]}"}
                code = site_code

            results[code] = res
            elapsed = res.get("elapsed", 0)
            status_icon = "✅" if "ok=" in res.get("output", "") and "ok=0" not in res["output"] else \
                          "⏱" if res.get("timeout") else "❌"
            print(f"  [{completed}/{len(retail_sites)}] {status_icon} {code} ({elapsed}s) {res.get('output', '')[:50]}")

            if res.get("timeout"):
                total_timeout += 1
                state["timeout_sites"] = state.get("timeout_sites", []) + [code]
            elif "ok=" in res.get("output", "") and "ok=0" not in res["output"]:
                total_ok += 1
            elif "blocked" in res.get("output", "").lower():
                total_blocked += 1
                state["blocked_sites"] = state.get("blocked_sites", []) + [code]
            else:
                retries = state.get("retry_count", {}).get(code, 0)
                if retries < state.get("max_retries", 2):
                    state.setdefault("retry_count", {})[code] = retries + 1
                    state["failed_sites"] = state.get("failed_sites", []) + [code]

    total_elapsed = round(time.time() - t_start, 1)
    state["retail_results"] = results
    state["stage"] = "review"
    print(f"\n[RETAIL] 完成: ok={total_ok} timeout={total_timeout} blocked={total_blocked} "
          f"总耗时={total_elapsed}s (并行度={parallel_workers})")
    return state
