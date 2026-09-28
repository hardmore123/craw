"""spec_node — SPEC抓取节点，调用spec_crawl（并行版）。

边界条件：
- 连续5系列失败 → 品牌熔断（spec_crawl内置）
- 磁盘满 → 暂停
- 并行度: 默认2站同时执行（SPEC抓取较重，并行度低于RETAIL）
- 每站独立子进程+独立DB连接
"""
from __future__ import annotations
import os, sys, subprocess, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from ..state import CrawlState

PY = r"C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"
DEFAULT_PARALLEL = 2  # SPEC并行度低于RETAIL(抓取更重)


def _run_spec(site: str, db_path: str, timeout: int = 600) -> dict:
    """在子进程中运行spec_crawl。"""
    script = f"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('OVERSEAS_PROXY', 'http://127.0.0.1:7877')
os.environ.setdefault('OVERSEAS_HEADLESS', 'true')
from overseas.db import Database
from overseas.scenarios import spec_crawl
db = Database('{db_path}')
db.init_schema()
try:
    st = spec_crawl('{site}', db=db, verbose=True)
    print(f'Result: ok={{st.ok}} fail={{st.fail}} blocked={{st.blocked}} products={{st.products}} rows={{getattr(st,"rows",0)}}')
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


def _run_one_spec(args: tuple) -> tuple[str, dict]:
    """并行执行单元。"""
    site, db_path, timeout = args
    t0 = time.time()
    res = _run_spec(site, db_path, timeout)
    elapsed = round(time.time() - t0, 1)
    res["elapsed"] = elapsed
    return site, res


def spec_node(state: CrawlState) -> CrawlState:
    """SPEC抓取节点：并行遍历spec_sites，对每站运行spec_crawl。"""
    db_path = state.get("db_path", "data/overseas.db")
    spec_sites = state.get("spec_sites", [])
    parallel_workers = state.get("spec_parallel_workers", DEFAULT_PARALLEL)
    results = state.get("spec_results", {})

    if not spec_sites:
        print("[SPEC] 无SPEC站，跳过")
        state["stage"] = "retail"
        return state

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    print(f"[SPEC] {len(spec_sites)}站，并行度={parallel_workers}")

    tasks = [(code, db_path, 600) for code in spec_sites]
    total_ok = total_fail = 0
    completed = 0
    t_start = time.time()

    with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
        future_to_site = {executor.submit(_run_one_spec, task): task[0] for task in tasks}

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
            # 解析结果
            output = res.get("output", "")
            ok = 0
            fail = 0
            if "ok=" in output:
                try:
                    ok = int(output.split("ok=")[1].split()[0])
                    fail = int(output.split("fail=")[1].split()[0]) if "fail=" in output else 0
                except:
                    pass

            status_icon = "✅" if ok > 0 else "⏱" if res.get("timeout") else "❌"
            print(f"  [{completed}/{len(spec_sites)}] {status_icon} {code} ({elapsed}s) ok={ok} fail={fail}")

            total_ok += ok
            total_fail += fail
            if ok == 0 and fail > 0 and not res.get("timeout"):
                state["failed_sites"] = state.get("failed_sites", []) + [code]

    total_elapsed = round(time.time() - t_start, 1)
    state["spec_results"] = results
    state["stage"] = "retail"
    print(f"\n[SPEC] 完成: ok={total_ok} fail={total_fail} 总耗时={total_elapsed}s (并行度={parallel_workers})")
    return state
