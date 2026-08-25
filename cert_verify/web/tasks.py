"""任务管理：内存任务表 + 后台核对线程。"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from ..config import ColumnMap, RunConfig
from ..excel_writer import write_crawl_copy, write_results
from ..models import BLOCKED
from ..verifier import RowOutcome, VerifyEngine, summarize_outcomes

WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_work")


@dataclass
class Task:
    id: str
    src_xlsx: str
    out_xlsx: str                     # 对比表格（结论+标红）
    out_crawl: str = ""               # 抓取表格（官网值）
    status: str = "pending"           # pending/running/completed/stopped/failed
    total: int = 0
    done: int = 0
    cur_sheet: str = ""
    cur_cert: str = ""
    cur_agency: str = ""
    rows: list = field(default_factory=list)     # list[RowOutcome]
    error: str = ""
    stop_reason: str = ""             # blocked / user_cancel / ""
    degraded: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------- 进度快照 ----------
    def snapshot(self, offset: int = 0) -> dict:
        with self._lock:
            new_rows = [o.to_json() for o in self.rows[offset:]]
            stats = summarize_outcomes(self.rows)
            return {
                "task_id": self.id,
                "status": self.status,
                "total": self.total,
                "done": self.done,
                "cur_sheet": self.cur_sheet,
                "cur_cert": self.cur_cert,
                "cur_agency": self.cur_agency,
                "stats": stats,
                "stop_reason": self.stop_reason,
                "error": self.error,
                "degraded": self.degraded,
                "new_rows": new_rows,
                "next_offset": len(self.rows),
                "download_ready": self.status in ("completed", "stopped")
                and os.path.exists(self.out_xlsx),
                "crawl_ready": self.status in ("completed", "stopped")
                and bool(self.out_crawl) and os.path.exists(self.out_crawl),
            }


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def create_and_start(self, src_xlsx: str, cfg_kwargs: dict,
                          colmaps: dict[str, ColumnMap], sheets: list[str]) -> Task:
        task_id = uuid.uuid4().hex[:12]
        base = os.path.splitext(os.path.basename(src_xlsx))[0]
        d = os.path.dirname(src_xlsx)
        out_xlsx = os.path.join(d, f"{base}_对比结果.xlsx")
        out_crawl = os.path.join(d, f"{base}_抓取结果.xlsx")
        task = Task(id=task_id, src_xlsx=src_xlsx, out_xlsx=out_xlsx,
                    out_crawl=out_crawl, status="pending")
        with self._lock:
            self._tasks[task_id] = task

        t = threading.Thread(target=self._run, args=(task, cfg_kwargs, colmaps, sheets),
                             daemon=True)
        t.start()
        return task

    def cancel(self, task_id: str) -> str:
        task = self.get(task_id)
        if not task:
            return "not_found"
        if task.status in ("completed", "failed", "stopped"):
            return task.status
        task.cancel.set()
        return "cancelling"

    # ---------- 后台执行 ----------
    def _run(self, task: Task, cfg_kwargs: dict,
             colmaps: dict[str, ColumnMap], sheets: list[str]) -> None:
        try:
            # 每 sheet 一个 colmap：用 SHEET_COLUMNS 等价的映射注入 RunConfig
            cfg = RunConfig(
                xlsx_path=task.src_xlsx, output_path=task.out_xlsx,
                sheets=sheets, **cfg_kwargs,
            )
            engine = _MultiMapEngine(cfg, colmaps)
            task.total = engine.count_rows()
            task.status = "running"

            outcomes_by_sheet: dict[str, list] = {}
            detail_col = cfg_kwargs.get("write_detail_col", "")

            for o in engine.iter_rows():
                if task.cancel.is_set():
                    task.stop_reason = "user_cancel"
                    break
                with task._lock:
                    task.rows.append(o)
                    task.done = len(task.rows)
                    task.cur_sheet = o.sheet
                    task.cur_cert = o.cert_no
                    task.cur_agency = o.agency
                outcomes_by_sheet.setdefault(o.sheet, []).append(o)
                if o.status == BLOCKED and cfg.stop_on_blocked:
                    task.stop_reason = "blocked"
                    break
                time.sleep(cfg.row_delay)

            # 提前 break 时也要释放 UL 浏览器会话
            engine._close_router()

            # 无论完成/取消/拦截，已完成部分都落盘：产出两份表格
            os.makedirs(os.path.dirname(os.path.abspath(task.out_xlsx)), exist_ok=True)
            dcol = detail_col or "N"
            # 1) 对比表格：原值不动 + 结论(一致/不一致/未查到) + 不一致红字 + 来源URL
            info = write_results(task.src_xlsx, task.out_xlsx, outcomes_by_sheet,
                                 detail_col=dcol)
            # 2) 抓取表格：原布局 + 官网抓取值(蓝字) + 查询状态 + 来源URL
            info2 = write_crawl_copy(task.src_xlsx, task.out_crawl, outcomes_by_sheet,
                                     detail_col=dcol)
            msgs = [m for m in (info.get("message"), info2.get("message")) if m]
            if msgs:
                task.degraded = "；".join(msgs)

            task.status = "stopped" if task.stop_reason else "completed"
        except Exception as e:  # noqa: BLE001
            task.error = f"{type(e).__name__}: {e}"
            task.status = "failed"


class _MultiMapEngine(VerifyEngine):
    """支持「每 sheet 一个 ColumnMap」的引擎。"""

    def __init__(self, cfg: RunConfig, colmaps: dict[str, ColumnMap]):
        super().__init__(cfg)
        self._colmaps = colmaps

    def _map_for(self, sheet: str) -> ColumnMap:
        return self._colmaps.get(sheet) or self.cfg.column_map_for(sheet)

    # 覆盖 count_rows / iter_rows 里对 column_map_for 的调用
    def count_rows(self) -> int:
        from openpyxl import load_workbook

        wb = load_workbook(self.cfg.xlsx_path, read_only=True, data_only=True)
        total = 0
        try:
            sheets = self.cfg.sheets or wb.sheetnames
            for name in sheets:
                if name not in wb.sheetnames:
                    continue
                ws = wb[name]
                cmap = self._map_for(name)
                col = _col(cmap.cert_no)
                cnt = 0
                for r in range(cmap.first_data_row, ws.max_row + 1):
                    v = ws.cell(row=r, column=col).value
                    if v is not None and str(v).strip():
                        cnt += 1
                total += cnt
        finally:
            wb.close()
        if self.cfg.limit:
            total = min(total, self.cfg.limit * max(1, len(self.cfg.sheets or [1])))
        self._total = total
        return total

    def iter_rows(self):
        from openpyxl import load_workbook

        wb = load_workbook(self.cfg.xlsx_path, read_only=True, data_only=True)
        try:
            sheets = self.cfg.sheets or wb.sheetnames
            for name in sheets:
                if name not in wb.sheetnames:
                    continue
                ws = wb[name]
                cmap = self._map_for(name)
                produced = 0
                for row in range(cmap.first_data_row, ws.max_row + 1):
                    if self.cfg.limit and produced >= self.cfg.limit:
                        break
                    cert_no = self._cell(ws, row, cmap.cert_no)
                    if not cert_no:
                        continue
                    outcome = self._verify_row(ws, name, row, cert_no, cmap)
                    produced += 1
                    yield outcome
                    if outcome.status == BLOCKED and self.cfg.stop_on_blocked:
                        return
        finally:
            wb.close()
            self._close_router()


def _col(letter: str) -> int:
    n = 0
    for ch in (letter or "").strip().upper():
        n = n * 26 + (ord(ch) - 64)
    return n
