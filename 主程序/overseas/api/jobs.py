"""任务管理：异步触发采集，串行执行。

为什么串行：
  1) 抓取本身必须域名级限速，并行不会更快，只会更容易被封；
  2) SQLite 写并发弱，多个采集进程同时写库会 database is locked。
所以用单个后台工作线程 + FIFO 队列，逐个用子进程跑脚本/CLI。

任务状态：queued → running → succeeded / failed / cancelled
日志按任务写到 data/logs/job_<id>.log，可增量拉取（前端轮询）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .. import config
from ..regions_config import get_region

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 日本线的 SPEC 品牌仍由历史配置维护；其它地区从集中区域配置读取。
JP_SPEC_BRANDS = {"hisense_jp", "sony_jp", "regza_jp",
                  "panasonic_jp", "tcl_jp", "sharp_jp"}


def _region_of(params: dict[str, Any] | None) -> str:
    """统一读取任务地区；缺省保持日本线兼容行为。"""
    value = (params or {}).get("region")
    return str(value or "jp").strip().lower()


def _region_brand_codes(region: str) -> set[str]:
    if region == "jp":
        return set(JP_SPEC_BRANDS)
    cfg = get_region(region)
    return set(cfg.brand_codes()) if cfg else set()


def _region_spec_codes(region: str) -> set[str]:
    if region == "jp":
        return set(JP_SPEC_BRANDS)
    cfg = get_region(region)
    return set(cfg.spec_site_codes()) if cfg else set()


def _region_retail_codes(region: str) -> set[str]:
    cfg = get_region(region)
    return set(cfg.retail_site_codes()) if cfg else set()


def _resolve_python() -> str:
    """选一个装了 Playwright 的 Python 解释器跑采集子进程。

    抓取子进程需要 Playwright；若服务被一个没装 Playwright 的解释器拉起
    （如本机同时装了 3.12 装了 Playwright、3.14 没装），直接用 sys.executable
    会让子进程报 "需要 Playwright"。解析优先级：
      1) 环境变量 OVERSEAS_PYTHON（显式指定）
      2) 当前 sys.executable（若它能 import playwright）
      3) 常见候选路径里第一个能 import playwright 的
      4) 兜底 sys.executable
    """
    import subprocess as _sp

    def _has_pw(exe: str) -> bool:
        try:
            r = _sp.run([exe, "-c", "import playwright"], capture_output=True, timeout=15)
            return r.returncode == 0
        except Exception:
            return False

    explicit = (os.environ.get("OVERSEAS_PYTHON") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    if _has_pw(sys.executable):
        return sys.executable
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Programs", "Python", "Python312", "python.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Programs", "Python", "Python313", "python.exe"),
        "python3", "python",
    ]
    for exe in candidates:
        if exe and (os.path.exists(exe) or not os.path.sep in exe) and _has_pw(exe):
            return exe
    return sys.executable


# 采集子进程用的 Python 解释器（启动时解析一次）
PY = _resolve_python()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _task_timeout(job: "Job") -> float:
    if job.task == "spec":
        return config.SPEC_TIMEOUT_SECONDS
    if job.task == "spec_refresh":
        # refresh_spec 会依次处理品牌；按选中品牌数给总上限，缺省为六品牌。
        count = len(job.params.get("brands") or []) or 6
        return config.SPEC_TIMEOUT_SECONDS * count
    if job.task == "review":
        if _region_of(job.params) != "jp":
            # CA 等零售线一个任务同时跑多个零售站，不能沿用单品牌 kakaku 上限。
            return config.REVIEW_TIMEOUT_SECONDS * 2
        # 多品牌（7.5）时按品牌数放大总上限；并发会缩短墙钟，这里给保守上限。
        brands = job.params.get("brands") or []
        count = len(brands) or 1
        concurrency = max(1, int(job.params.get("concurrency") or 1))
        # 并发时总墙钟约按 ceil(count/concurrency) 个品牌串行估算。
        effective = -(-count // concurrency)
        return config.REVIEW_TIMEOUT_SECONDS * max(1, effective)
    if job.task == "price" and _region_of(job.params) != "jp":
        return config.REVIEW_TIMEOUT_SECONDS * 2
    if job.task == "ca_retail":
        # 多零售站 × 多型号同步抓取，墙钟较长；给较宽松上限。
        return config.REVIEW_TIMEOUT_SECONDS * 2
    # 价格/发售日同样不能无限等待，沿用较宽松的网评任务上限。
    return config.REVIEW_TIMEOUT_SECONDS


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """终止任务子进程及其浏览器子树，避免父 Python 退出后 Edge 残留。"""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=config.JOB_TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=config.JOB_TERMINATE_GRACE_SECONDS,
                check=False,
            )
        except Exception:
            pass
    else:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=config.JOB_TERMINATE_GRACE_SECONDS)
    except Exception:
        pass


# 允许触发的任务类型 → 命令构造函数。
def _job_db_path(params: dict[str, Any]) -> str:
    return str(params.get("_db_path") or config.DB_PATH)


# 只允许白名单任务，参数经过显式拼装，不接受前端传任意命令（防命令注入）。
def _spec_cmd(params: dict[str, Any]) -> list[str]:
    cmd = [PY, "-m", "overseas.cli", "--db", _job_db_path(params),
           "spec-crawl", "--site", str(params["brand"])]
    for series in params.get("series") or []:
        cmd += ["--series", str(series)]
    return cmd


def _validate_task_params(task: str, params: dict[str, Any]) -> None:
    """在入队前校验地区、品牌和站点，避免异步任务跨线或静默忽略。"""
    region = _region_of(params)
    if region != "jp" and get_region(region) is None:
        raise ValueError(f"不支持的地区: {region}")
    if task == "ca_retail" and region == "jp":
        raise ValueError("ca_retail 仅支持非日本地区；日本线请使用 price 或 review")

    allowed_brands = _region_brand_codes(region)
    brands = [str(value).strip() for value in (params.get("brands") or [])
              if str(value).strip()]
    if params.get("brand"):
        brands.append(str(params["brand"]).strip())
    invalid_brands = sorted(set(brands) - allowed_brands)
    if invalid_brands:
        raise ValueError(f"品牌不属于地区 {region}: {', '.join(invalid_brands)}")

    if task == "spec":
        brand = str(params.get("brand") or "").strip()
        if not brand:
            raise ValueError("spec 任务缺少 brand")
        if brand not in _region_spec_codes(region):
            raise ValueError(f"SPEC 品牌不属于地区 {region}: {brand}")
    if task == "release" and region != "jp":
        raise ValueError("release 发售日任务仅支持 region=jp，加拿大线暂不支持")
    if task == "release" and params.get("brands"):
        raise ValueError("release 任务只接受单个 brand，不接受 brands")
    if region != "jp" and task in {"price", "review", "ca_retail"}:
        if params.get("year") is not None:
            raise ValueError("非日本价格/评价任务不支持 year；加拿大线没有发售年份筛选")

    sites = [str(value).strip() for value in (params.get("sites") or [])
             if str(value).strip()]
    if sites:
        allowed_sites = {"kakaku_jp"} if region == "jp" else _region_retail_codes(region)
        invalid_sites = sorted(set(sites) - allowed_sites)
        if invalid_sites:
            raise ValueError(f"站点不属于地区 {region}: {', '.join(invalid_sites)}")


def _spec_refresh_cmd(params: dict[str, Any]) -> list[str]:
    cmd = [PY, os.path.join(PROJECT_ROOT, "scripts", "refresh_spec.py"),
           "--db", _job_db_path(params)]
    region = _region_of(params)
    if region != "jp":
        cmd += ["--region", region]
    for brand in params.get("brands") or []:
        cmd += ["--site", str(brand)]
    if params.get("export_only"):
        cmd.append("--export-only")
    return cmd


def _ca_retail_cmd(params: dict[str, Any]) -> list[str]:
    """非日本线价格+网评同步抓取，并写入服务绑定的通用数据库。"""
    _validate_task_params("ca_retail", params)
    cmd = [PY, os.path.join(PROJECT_ROOT, "scripts", "crawl_ca_retail.py"),
           "--region", _region_of(params),
           "--save-db", "--db", _job_db_path(params)]
    brands = params.get("brands") or []
    if not brands and params.get("brand"):
        brands = [params["brand"]]
    for brand in brands:
        cmd += ["--brand", str(brand)]
    sites = [str(site) for site in (params.get("sites") or []) if str(site).strip()]
    if sites:
        # crawl_ca_retail.py 的 --sites 是 nargs="*"，必须一次传入全部站点。
        cmd += ["--sites", *sites]
    models = params.get("models") or []
    if models:
        cmd += ["--models", *[str(m) for m in models]]
    if params.get("models_csv"):
        cmd += ["--models-csv", str(params["models_csv"])]
    if params.get("limit_models"):
        cmd += ["--limit-models", str(int(params["limit_models"]))]
    if params.get("max_reviews") is not None:
        cmd += ["--max-reviews", str(int(params["max_reviews"]))]
    if params.get("out_dir"):
        cmd += ["--out-dir", str(params["out_dir"])]
    return cmd


def _price_cmd(params: dict[str, Any]) -> list[str]:
    # 非日本线（加拿大等）价格与网评同源，走统一的零售同步抓取脚本。
    if _region_of(params) != "jp":
        return _ca_retail_cmd(params)
    cmd = [PY, os.path.join(PROJECT_ROOT, "scripts", "crawl_kakaku_prices.py"),
           "--save-db", "--db", _job_db_path(params)]
    for brand in params.get("brands") or []:
        cmd += ["--brand", str(brand)]
    if params.get("year") is not None:
        cmd += ["--year", str(int(params["year"]))]
    if params.get("limit_models"):
        cmd += ["--limit-models", str(int(params["limit_models"]))]
    return cmd


def _review_cmd(params: dict[str, Any]) -> list[str]:
    # 非日本线（加拿大等）网评与价格同源，走统一的零售同步抓取脚本。
    if _region_of(params) != "jp":
        return _ca_retail_cmd(params)
    cmd = [PY, os.path.join(PROJECT_ROOT, "scripts", "crawl_kakaku_reviews.py"),
           "--save-db", "--db", _job_db_path(params)]
    # 多品牌（7.5）优先：params.brands 非空时用 --brands + --concurrency。
    brands = params.get("brands") or []
    if brands:
        cmd += ["--brands", *[str(b) for b in brands]]
        if params.get("concurrency"):
            cmd += ["--concurrency", str(int(params["concurrency"]))]
    else:
        cmd += ["--brand", str(params.get("brand") or "hisense_jp")]
    if params.get("year") is not None:
        cmd += ["--year", str(int(params["year"]))]
    models = params.get("models") or []
    if models:
        cmd += ["--models", *[str(m) for m in models]]
    if params.get("limit_models"):
        cmd += ["--limit-models", str(int(params["limit_models"]))]

    # ---- 7.4 前端可控等待时间（毫秒/秒），仅在显式给出时下传 ----
    _int_flags = {
        "settle_ms": "--settle-ms",
        "scroll_passes": "--scroll-passes",
        "scroll_wait_ms": "--scroll-wait-ms",
        "nav_timeout_ms": "--nav-timeout-ms",
        "entry_nav_timeout_ms": "--entry-nav-timeout-ms",
    }
    for key, flag in _int_flags.items():
        if params.get(key) is not None:
            cmd += [flag, str(int(params[key]))]
    if params.get("min_interval") is not None:
        cmd += ["--min-interval", str(float(params["min_interval"]))]

    # ---- 7.1 / 7.2 / 7.6 / 7.3 降耗开关（仅在显式给出时下传） ----
    if params.get("release_cache") in ("off", "prefer", "refresh"):
        cmd += ["--release-cache", str(params["release_cache"])]
    if params.get("release_cache_max_age_days") is not None:
        cmd += ["--release-cache-max-age-days",
                str(int(params["release_cache_max_age_days"]))]
    if params.get("thread_incremental") in ("on", "off"):
        cmd += ["--thread-incremental", str(params["thread_incremental"])]
    if params.get("thread_recheck_days") is not None:
        cmd += ["--thread-recheck-days", str(int(params["thread_recheck_days"]))]
    if params.get("skip_known_no_item"):
        cmd += ["--skip-known-no-item"]
    ai_requested = any(params.get(key) for key in
                       ("translate", "extract_pros", "extract_cons"))
    if ai_requested:
        engine = str(params.get("translate_engine") or "nllb").strip().lower()
        if engine in {"nllb", "argos", "xinghai"}:
            cmd += ["--translate-engine", engine]
    if params.get("translate"):
        cmd.append("--translate")
    if params.get("extract_pros"):
        cmd.append("--extract-pros")
    if params.get("extract_cons"):
        cmd.append("--extract-cons")
    if params.get("page_cache") in ("off", "on"):
        cmd += ["--page-cache", str(params["page_cache"])]
    return cmd


def _release_cmd(params: dict[str, Any]) -> list[str]:
    _validate_task_params("release", params)
    brand = str(params.get("brand") or "hisense_jp").strip()
    data_dir = os.path.dirname(os.path.abspath(_job_db_path(params)))
    out_path = os.path.join(data_dir, f"发售日_{brand}.json")
    return [PY, os.path.join(PROJECT_ROOT, "scripts", "crawl_kakaku_release.py"),
            "--brand", brand, "--out", out_path]


TASK_BUILDERS = {
    "spec": _spec_cmd,
    "spec_refresh": _spec_refresh_cmd,
    "price": _price_cmd,
    "review": _review_cmd,
    "release": _release_cmd,
    # 加拿大等非日本线：价格 + 网评同源，一条任务同步产出两者。
    "ca_retail": _ca_retail_cmd,
}


@dataclass
class Job:
    id: str
    task: str
    params: dict[str, Any]
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    message: str = ""
    log_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        public_params = {k: v for k, v in self.params.items()
                         if not k.startswith("_")}
        return {
            "id": self.id, "task": self.task, "params": public_params,
            "status": self.status, "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "exit_code": self.exit_code, "message": self.message,
        }


class JobManager:
    """单工作线程 + FIFO 队列的任务管理器（进程内单例）。"""

    def __init__(self, db_path: str | None = None):
        self.db_path = os.path.abspath(os.path.expanduser(db_path or config.DB_PATH))
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._current: str | None = None
        self._proc: subprocess.Popen | None = None
        self._worker: threading.Thread | None = None
        os.makedirs(config.LOG_DIR, exist_ok=True)

    def set_db_path(self, db_path: str) -> None:
        """服务启动时绑定查询库；运行中不允许切换，避免任务写错库。"""
        path = os.path.abspath(os.path.expanduser(db_path))
        with self._lock:
            if self._current or self._queue:
                raise RuntimeError("任务运行或排队中，不能切换数据库")
            self.db_path = path

    # ---------------- 提交与查询 ----------------

    def submit(self, task: str, params: dict[str, Any] | None = None) -> Job:
        if task not in TASK_BUILDERS:
            raise ValueError(f"不支持的任务类型: {task}（可用: {sorted(TASK_BUILDERS)}）")
        job_params = dict(params or {})
        _validate_task_params(task, job_params)
        # 内部路径由服务绑定，忽略前端同名字段，防止查询库与写入库分叉。
        job_params["_db_path"] = self.db_path
        job = Job(id=uuid.uuid4().hex[:12], task=task, params=job_params)
        job.log_path = os.path.join(config.LOG_DIR, f"job_{job.id}.log")
        with self._wake:
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._ensure_worker()
            self._wake.notify()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict() for j in jobs[:limit]]

    def log(self, job_id: str, offset: int = 0, max_bytes: int = 64_000) -> dict[str, Any]:
        """增量拉日志：返回从 offset 起的内容与新 offset，供前端轮询。"""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if not job.log_path or not os.path.exists(job.log_path):
            return {"id": job_id, "offset": offset, "content": "", "eof": True}
        size = os.path.getsize(job.log_path)
        with open(job.log_path, "rb") as handle:
            handle.seek(max(0, offset))
            data = handle.read(max_bytes)
        return {
            "id": job_id,
            "offset": (offset if offset > 0 else 0) + len(data),
            "size": size,
            "content": data.decode("utf-8", "replace"),
            "eof": (offset + len(data)) >= size and job.status in
                   ("succeeded", "failed", "cancelled"),
        }

    def cancel(self, job_id: str) -> bool:
        """取消任务：排队中直接移除；运行中终止子进程。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status == "queued":
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job.status = "cancelled"
                job.finished_at = _now()
                job.message = "排队中被取消"
                return True
            if job.status == "running" and self._current == job_id and self._proc:
                try:
                    _terminate_process_tree(self._proc)
                    job.message = "运行中被取消"
                    return True
                except Exception:
                    return False
        return False

    def queue_info(self) -> dict[str, Any]:
        with self._lock:
            return {"running": self._current, "queued": list(self._queue),
                    "total_jobs": len(self._jobs)}

    # ---------------- 工作线程 ----------------

    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run_loop, daemon=True,
                                            name="job-worker")
            self._worker.start()

    def _run_loop(self) -> None:
        while True:
            with self._wake:
                while not self._queue:
                    # 空闲 60 秒后退出线程，下次 submit 会重新拉起
                    if not self._wake.wait(timeout=60):
                        if not self._queue:
                            return
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                self._current = job_id
                job.status = "running"
                job.started_at = _now()
            self._execute(job)
            with self._lock:
                self._current = None
                self._proc = None

    def _execute(self, job: Job) -> None:
        try:
            cmd = TASK_BUILDERS[job.task](job.params)
        except Exception as e:
            job.status = "failed"
            job.finished_at = _now()
            job.message = f"参数错误: {type(e).__name__}: {e}"
            return
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # 子进程 stdout 重定向到日志文件（非 TTY），Python 默认块缓冲会让 print
        # 长时间不落盘，导致前端日志/进度条一直空白。强制无缓冲以实时刷新。
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("OVERSEAS_NAV_TIMEOUT", "60")
        timeout = _task_timeout(job)
        proc: subprocess.Popen | None = None
        try:
            with open(job.log_path, "wb") as log:
                log.write(f"[CMD] {' '.join(cmd)}\n".encode("utf-8"))
                log.flush()
                proc = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=env,
                                        stdout=log, stderr=subprocess.STDOUT)
                with self._lock:
                    self._proc = proc
                code = proc.wait(timeout=timeout)
            job.exit_code = code
            if job.message.endswith("被取消"):
                job.status = "cancelled"
            else:
                job.status = "succeeded" if code == 0 else "failed"
                job.message = job.message or (f"退出码 {code}" if code else "完成")
        except subprocess.TimeoutExpired:
            if proc is not None:
                _terminate_process_tree(proc)
            job.exit_code = 124
            job.status = "failed"
            job.message = f"任务超过硬超时 {timeout:.0f}s，已终止进程树"
        except Exception as e:
            job.status = "failed"
            job.message = f"{type(e).__name__}: {e}"
        finally:
            job.finished_at = _now()


# 进程内单例
manager = JobManager()


# ============================================================
# 定时任务：把「任务 + 参数 + 触发规则」持久化，后台线程按分钟检查并入队。
# 规则支持两种：
#   {"kind":"interval","every_hours":24}                 每 N 小时
#   {"kind":"weekly","weekday":6,"hour":3,"minute":20}   每周某天某时（weekday 0=周一,6=周日）
# 时间按本地时区判断。触发时调用 manager.submit 入队（仍串行执行）。
# ============================================================

SCHEDULE_FILE = os.path.join(config.DATA_DIR, "schedules.json")


def _local_now() -> datetime:
    # 调度器统一使用带时区的 UTC，避免与 crawl/job 的 `_now()` 比较时
    # 出现 aware/naive TypeError；前端仍可按 ISO 时间展示/转换本地时区。
    return datetime.now(timezone.utc)


@dataclass
class Schedule:
    id: str
    task: str
    params: dict[str, Any]
    trigger: dict[str, Any]
    enabled: bool = True
    name: str = ""
    created_at: str = field(default_factory=_now)
    last_run_at: str = ""
    last_job_id: str = ""
    next_run_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "task": self.task,
            "params": self.params, "trigger": self.trigger,
            "enabled": self.enabled, "created_at": self.created_at,
            "last_run_at": self.last_run_at, "last_job_id": self.last_job_id,
            "next_run_at": self.next_run_at,
        }


class Scheduler:
    """定时调度：持久化到 data/schedules.json，后台线程每 30 秒检查一次。"""

    def __init__(self, job_manager: JobManager):
        self._jm = job_manager
        self._items: dict[str, Schedule] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load()

    # ---------------- 持久化 ----------------
    def _load(self) -> None:
        if not os.path.exists(SCHEDULE_FILE):
            return
        try:
            with open(SCHEDULE_FILE, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        for item in raw or []:
            try:
                sch = Schedule(
                    id=item["id"], task=item["task"], params=item.get("params") or {},
                    trigger=item.get("trigger") or {}, enabled=item.get("enabled", True),
                    name=item.get("name", ""), created_at=item.get("created_at", _now()),
                    last_run_at=item.get("last_run_at", ""),
                    last_job_id=item.get("last_job_id", ""),
                    next_run_at=item.get("next_run_at", ""))
                if sch.enabled and not sch.next_run_at:
                    sch.next_run_at = self._compute_next(sch, _local_now())
                self._items[sch.id] = sch
            except (KeyError, TypeError):
                continue

    def _save(self) -> None:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as handle:
            json.dump([s.to_dict() for s in self._items.values()], handle,
                      ensure_ascii=False, indent=2)

    # ---------------- CRUD ----------------
    def add(self, task: str, params: dict[str, Any], trigger: dict[str, Any],
            name: str = "", enabled: bool = True) -> Schedule:
        if task not in TASK_BUILDERS:
            raise ValueError(f"不支持的任务类型: {task}")
        self._validate_trigger(trigger)
        params = dict(params or {})
        _validate_task_params(task, params)
        sch = Schedule(id=uuid.uuid4().hex[:12], task=task, params=params,
                       trigger=dict(trigger), name=name, enabled=enabled)
        with self._lock:
            sch.next_run_at = self._compute_next(sch, _local_now())
            self._items[sch.id] = sch
            self._save()
        return sch

    def update(self, sid: str, **fields) -> Schedule | None:
        with self._lock:
            sch = self._items.get(sid)
            if sch is None:
                return None
            if "enabled" in fields:
                sch.enabled = bool(fields["enabled"])
            if "trigger" in fields and fields["trigger"]:
                self._validate_trigger(fields["trigger"])
                sch.trigger = dict(fields["trigger"])
            if "params" in fields and isinstance(fields["params"], dict):
                new_params = dict(fields["params"])
                _validate_task_params(sch.task, new_params)
                sch.params = new_params
            if "name" in fields:
                sch.name = str(fields["name"])
            sch.next_run_at = self._compute_next(sch, _local_now())
            self._save()
            return sch

    def remove(self, sid: str) -> bool:
        with self._lock:
            if sid in self._items:
                del self._items[sid]
                self._save()
                return True
            return False

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_dict() for s in self._items.values()]

    @staticmethod
    def _validate_trigger(trigger: dict[str, Any]) -> None:
        kind = (trigger or {}).get("kind")
        if kind == "interval":
            if int(trigger.get("every_hours", 0)) <= 0:
                raise ValueError("interval 触发需要 every_hours > 0")
        elif kind == "weekly":
            wd = int(trigger.get("weekday", -1))
            if not (0 <= wd <= 6):
                raise ValueError("weekly 触发 weekday 需在 0..6（0=周一,6=周日）")
        else:
            raise ValueError("trigger.kind 必须是 interval 或 weekly")

    # ---------------- 调度计算 ----------------
    def _compute_next(self, sch: Schedule, base: datetime) -> str:
        from datetime import timedelta
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        else:
            base = base.astimezone(timezone.utc)
        t = sch.trigger
        if t.get("kind") == "interval":
            hours = max(1, int(t.get("every_hours", 24)))
            anchor = base
            if sch.last_run_at:
                try:
                    anchor = datetime.fromisoformat(sch.last_run_at)
                    if anchor.tzinfo is None:
                        anchor = anchor.replace(tzinfo=timezone.utc)
                    else:
                        anchor = anchor.astimezone(timezone.utc)
                except ValueError:
                    anchor = base
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            else:
                base = base.astimezone(timezone.utc)
            nxt = anchor + timedelta(hours=hours)
            if nxt < base:
                nxt = base + timedelta(minutes=1)
            return nxt.isoformat(timespec="minutes")
        if t.get("kind") == "weekly":
            wd = int(t.get("weekday", 6))
            hour = int(t.get("hour", 3))
            minute = int(t.get("minute", 20))
            days_ahead = (wd - base.weekday()) % 7
            cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            from datetime import timedelta as _td
            cand += _td(days=days_ahead)
            if cand <= base:
                cand += _td(days=7)
            return cand.isoformat(timespec="minutes")
        return ""

    # ---------------- 后台线程 ----------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(30):
            now = _local_now()
            due: list[Schedule] = []
            with self._lock:
                for sch in self._items.values():
                    if not sch.enabled or not sch.next_run_at:
                        continue
                    try:
                        nxt = datetime.fromisoformat(sch.next_run_at)
                        if nxt.tzinfo is None:
                            nxt = nxt.replace(tzinfo=timezone.utc)
                        else:
                            nxt = nxt.astimezone(timezone.utc)
                    except ValueError:
                        continue
                    if nxt <= now:
                        due.append(sch)
            for sch in due:
                try:
                    job = self._jm.submit(sch.task, sch.params)
                    with self._lock:
                        sch.last_run_at = _now()
                        sch.last_job_id = job.id
                        sch.next_run_at = self._compute_next(sch, now)
                        self._save()
                except Exception:
                    continue


# 进程内单例（server.py 启动时 scheduler.start()）
scheduler = Scheduler(manager)
