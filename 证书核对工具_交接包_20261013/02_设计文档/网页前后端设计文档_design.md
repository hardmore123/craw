# Design Document

## Overview

在现有 `cert_verify` 包之上新增一个**本地网页前后端层**，不改动既有比对语义与 Provider 能力。后端用 Python 标准库 `http.server` 自建轻量 HTTP 服务（零额外依赖，便于打包与离线运行），复用 `ProviderRouter`、`compare_record`、`ColumnMap`、`RunConfig` 完成核对；前端为单页静态 HTML/CSS/JS（原生，无框架），通过 JSON API 与后端交互，用轮询获取进度。

核对逻辑与 Excel 写入被重构为可被 Web 复用的「引擎」：现有 `excel_runner.run()` 是一次性同步流程，无法上报进度、取消，也不写样式。设计中抽出一个**可迭代、可取消、产出结构化结果**的核对内核，Web 与 CLI 共用它；Excel 输出扩展为在副本上写值 + 红色字体标红。

### 设计目标与非目标

- 目标：本机单用户网页操作；实时进度；真不一致红色字体标红；保留原格式副本；UL 登录按钮弹 Edge；不外泄敏感数据。
- 非目标：多用户/鉴权、局域网共享、数据库持久化、并发大规模抓取、云部署。

### 为什么用标准库 http.server 而非 FastAPI

- 打包交付要求「开箱即用、少依赖」；`http.server` + `openpyxl`（已有）+ 可选 `playwright`（仅 UL）即可。
- 单用户本地场景无需异步框架的高并发能力；后台线程 + 轮询足够。
- 减少 pip 安装失败风险（内网环境）。

## Architecture

```
浏览器（单页 index.html + app.js + style.css）
        │  fetch JSON / 轮询
        ▼
cert_verify/web/server.py   —— ThreadingHTTPServer，仅绑 127.0.0.1:8756
        │  路由分发
        ├── /                     静态页
        ├── /api/upload           保存 xlsx + 返回 sheet/预览
        ├── /api/run              创建任务（后台线程）→ task_id
        ├── /api/progress?id=     轮询进度 + 增量结果
        ├── /api/cancel           请求取消
        ├── /api/ul-login         触发 ul_login（弹 Edge）
        ├── /api/ul-status        UL 登录态检测/登录进度
        └── /api/download?id=     下载结果 xlsx
        │
        ▼
cert_verify/verifier.py     —— 核对内核（Web/CLI 共用）
        │  逐行：ProviderRouter.query() → compare_record()
        │        产出 FieldVerdict[] + 行结论 + 累计统计
        ├── ProviderRouter（VDE/UL/TÜV，既有）
        ├── compare.py（既有，字段级比对）
        └── excel_writer.py —— 在原文件副本上写 M/N 值 + 红字标红
        │
        ▼
TaskManager（内存任务表：状态/进度/结果/取消标志/输出路径）
```

任务全程在内存中管理（单进程），输出文件落在受控工作目录 `cert_verify/web/_work/<task_id>/`。

## Components and Interfaces

### 1. 核对内核 `cert_verify/verifier.py`（新增，Web/CLI 共用）

把逐行核对从 `excel_runner.run()` 中抽象出来，使其可上报进度、可取消、产出结构化字段结论。

```python
@dataclass
class FieldVerdict:
    field: str            # manufacturer/factory/model/standard/cert_status
    label: str            # 中文标签
    sheet_value: str      # 表格值
    site_value: str       # 官网值
    verdict: str          # MATCH / MISMATCH / MISSING_SITE / MISSING_SHEET
    detail: str

@dataclass
class RowOutcome:
    sheet: str
    row: int
    cert_no: str
    agency: str           # 实际路由到的 Provider 名
    status: str           # CertRecord.status: found/not_found/blocked/error/unsupported
    consistent: bool      # 是否无 MISMATCH
    verdict_text: str     # 写入 M 列的摘要（含官网正确值）
    fields: list[FieldVerdict]
    source_url: str
    message: str

class VerifyEngine:
    def __init__(self, cfg: RunConfig): ...
    def iter_rows(self) -> Iterator[RowOutcome]:
        """惰性逐行核对；调用方可在每次产出后检查取消标志。"""
    # 供 Excel 写入层使用：把 RowOutcome 映射回单元格坐标
```

要点：
- `compare_record` 现返回 `(bool, str)`，为满足字段级标红，新增一个**并列函数** `compare_fields(sheet_row, rec) -> list[FieldVerdict]`，复用现有 `_CMP` 字段比较器；`compare_record` 保持不变（CLI 兼容），Web 与标红都走 `compare_fields`，再由其组装摘要文本，保证「同一份判断」。
- `verdict_text` 由 `compare_fields` 结果组装，MISMATCH 字段附官网正确值：`✗ 型号不一致；官网：A24-065N3A`。

### 2. Excel 写入层 `cert_verify/excel_writer.py`（新增）

```python
def write_results(src_xlsx: str, out_xlsx: str,
                  outcomes_by_sheet: dict[str, list[RowOutcome]],
                  colmap_by_sheet: dict[str, ColumnMap],
                  detail_col: str = "N") -> None
```

行为：
- 用 `openpyxl.load_workbook(src)`（非 read_only，保留样式/公式/合并）加载**原文件的副本源**，另存到 `out_xlsx`，绝不写回 `src`。
- 对每个 `RowOutcome`：
  - M 列写 `verdict_text`；`detail_col` 写 `source_url or message`。
  - 对 `fields` 中 `verdict == MISMATCH` 的字段，找到该字段在 `ColumnMap` 中的列字母；若有映射，则将对应单元格设为**红色字体**：`cell.font = copy(cell.font) + red`（保留原字号/加粗，仅改颜色 `FF0000`），不改填充与边框。
  - MISSING_SITE / MISSING_SHEET / 非 found 状态：不改字段单元格样式。
- 降级：若保存因样式/公式异常失败，捕获后退回「仅写值、不设样式」再存一次，并在返回信息中标注降级。

字体标红实现细节（openpyxl）：读取原 `cell.font`，用 `Font(name=..., size=..., bold=..., italic=..., color="FFFF0000")` 复制原属性仅替换颜色，避免丢失原字体。

### 3. Web 服务 `cert_verify/web/server.py`（新增）

- `ThreadingHTTPServer((‘127.0.0.1’, port), Handler)`；端口占用则 `port+1` 递增重试（req 1.3）。
- 启动后 `webbrowser.open` 打开页面（req 1.2）。
- `BaseHTTPRequestHandler` 子类按 path 分发；仅回环绑定（req 1.4/9）。
- 静态资源从 `cert_verify/web/static/` 读取并内联返回。

#### API 契约

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | /api/upload | multipart xlsx | `{file_id, sheets:[{name, preview:[[..]], max_row}]}` |
| POST | /api/run | `{file_id, sheets:[{name, colmap, enabled}], limit, proxy, default_agency, stop_on_blocked, detail_col}` | `{task_id}` |
| GET | /api/progress?id= | — | `{status, total, done, cur_sheet, cur_cert, cur_agency, stats, new_rows:[RowOutcome]}` |
| POST | /api/cancel | `{task_id}` | `{status}` |
| GET | /api/ul-status | — | `{logged_in, login_running, message}` |
| POST | /api/ul-login | — | `{started}` |
| GET | /api/download?id= | — | xlsx 附件流 |

- 上传只接受 `.xlsx`、限制大小（如 ≤ 30MB）（req 9.1）。
- `/api/download` 只按 `task_id` 映射到 `_work/<id>/` 内的输出文件，拒绝任意路径（防穿越）（req 9.2/9.4）。
- 进度用增量游标：前端记录已收行数 `offset`，服务端只回 `new_rows`，避免重复传输大列表（req 4）。

### 4. 任务管理 `TaskManager`（在 server.py 内或独立模块）

```python
@dataclass
class Task:
    id: str
    status: str          # pending/running/completed/stopped/failed
    cancel: threading.Event
    total: int
    done: int
    cur_sheet: str; cur_cert: str; cur_agency: str
    stats: dict           # found/not_found/blocked/consistent/review
    rows: list[RowOutcome]
    out_path: str
    error: str
    stop_reason: str      # blocked / user_cancel / ""
```

- `run()` 在后台线程里遍历 `VerifyEngine.iter_rows()`；每行更新 `done/cur_*/stats/rows`；每行检查 `cancel.is_set()`；遇 BLOCKED 且 `stop_on_blocked` 置 `stop_reason=blocked` 并跳出。
- 结束后调用 `excel_writer.write_results` 生成输出，置 `completed/stopped`。
- 取消：置 `cancel`，循环下一行前退出，仍写已完成部分（req 5.2）。
- 断线恢复：任务在内存，前端凭 `task_id` 重新轮询即可（req 4.6）。

### 5. UL 登录 `/api/ul-login` + `/api/ul-status`

- `ul-status`：检测 `PROFILE_DIR`（`.ul_profile/`）是否存在判断 `logged_in`（复用 `ul.py` 已有逻辑）（req 8.1）。
- `ul-login`：在后台线程调用 `cert_verify.ul_login.login()`，弹出本机 Edge（有头）供用户登录（req 8.2）。以标志位 `login_running` 表示进行中，登录函数返回后重新检测 `.ul_profile` 更新 `logged_in`。
- 绝不通过任何 API 返回 `.ul_profile` 内容或 Cookie（req 8.3/9.3）。
- 若查询中 UL 返回 BLOCKED 且提示登录失效，行结果 message 透传「登录态可能失效，请重登」（req 8.4，既有 `ul.py` 已产生该消息）。

### 6. 前端 `cert_verify/web/static/`（index.html / app.js / style.css）

单页四个阶段（同页分区，不跳转）：
1. **上传区**：选择 xlsx → `/api/upload` → 显示 sheet 列表与预览。
2. **列映射区**：每个 sheet 一张列映射表单（证书号 I、结果 M、制造商/生产厂/型号/标准/状态/机构列、起始行），可勾选是否参与、可「套用到全部」。运行参数（limit、proxy、default_agency、stop_on_blocked、detail_col）。含「UL 登录」按钮与状态灯。
3. **进度区**：进度条 + 当前 sheet/证书号/机构 + 统计卡片（查到/未查到/被拦截/一致/需复核）+「取消」按钮。轮询 `/api/progress`。
4. **结果区**：结果表（sheet/行/证书号/机构/结论/状态），状态色：一致=绿、不一致=红字、无此项/未填=灰、未查到=橙、被拦截/失败=红底；筛选下拉（全部/仅不一致/仅未查到或被拦截）；点击行展开字段级明细（表格值 vs 官网值 vs 结论 + 来源链接）；「下载结果 Excel」。

## Data Models

复用既有 `CertRecord`、`ColumnMap`、`RunConfig`。新增 `FieldVerdict`、`RowOutcome`（见上）。前端 `RowOutcome` 以 JSON 序列化传输；`fields` 内含每字段的表格值/官网值/结论用于明细与标红定位。

列映射经 API 传入后转为 `ColumnMap` 实例；`RunConfig` 增补由 Web 传入的 `columns`（本任务用「每 sheet 一个 colmap」，因此 `RunConfig` 按 sheet 循环时用对应 colmap，沿用现有 `column_map_for`，Web 侧构造 `SHEET_COLUMNS` 等价的映射字典传入引擎）。

## 复用与改动清单

| 文件 | 改动 |
|---|---|
| `compare.py` | 新增 `compare_fields()` 返回 `list[FieldVerdict]`；`compare_record` 改为基于 `compare_fields` 组装（保持返回签名与文案兼容）。 |
| `verifier.py`（新） | `VerifyEngine.iter_rows()` 惰性逐行核对，产出 `RowOutcome`。 |
| `excel_writer.py`（新） | 在副本上写值 + 红字标红，含降级。 |
| `excel_runner.py` | `run()` 改为基于 `VerifyEngine`（保持 CLI 行为与输出不变），并调用 `excel_writer` 以获得标红能力。 |
| `web/server.py`（新） | HTTP 服务 + API + TaskManager。 |
| `web/__main__.py`（新） | `python -m cert_verify.web` 启动入口。 |
| `web/static/*`（新） | 前端页面。 |
| `build_package.py` | 纳入 web 目录、新增 `启动网页.bat`，更新文件清单/说明。 |
| `PACKAGE_README.md` / 介绍文档 | 增加网页用法与标红说明。 |

## Error Handling

- 上传非法/超限：`/api/upload` 返回 4xx + 中文提示，不建任务（req 2.6/9.1）。
- 无法解析工作簿：同上。
- 任务线程内异常：捕获置 `status=failed` + `error`，进度接口如实返回；已完成行仍尝试落盘。
- 官网查询：沿用 Provider 内部的 `not_found/blocked/error` 语义，逐行如实记录，不中断整表（除 `stop_on_blocked`）。
- Excel 保存失败：降级为仅写值（见 excel_writer），仍提供下载并标注降级。
- 端口占用：自动 +1 重试（req 1.3）。
- 下载非法 id/路径穿越：返回 404，不泄露路径（req 9.2/9.4）。

## Testing Strategy

- **单元测试**
  - `compare_fields`：构造 sheet_row + CertRecord，断言各字段 verdict（含 UL 型号 MISSING_SITE、VDE 通配 MATCH、标准年份差异 MATCH+注记、真实不一致 MISMATCH）。
  - `excel_writer`：用小样 xlsx，断言仅 MISMATCH 字段单元格字体变红且原字体属性保留；MISSING/未查到不变；原文件字节不变（哈希比对）。
  - 端口选择、下载路径穿越防护、上传类型校验的函数级测试。
- **集成测试（离线优先）**
  - 用 VDE fixture（`vde_40052271.json`）经 `parse` 造 CertRecord，跑 `VerifyEngine` 单行，验证 RowOutcome 与 M 列文案。
  - 用 monkeypatch 替换 `ProviderRouter.query` 返回预置 CertRecord，跑 `/api/run` → `/api/progress` → `/api/download` 全链路，避免真实联网。
- **手动验证**
  - 真实 `dist/input/...xlsx` 两个 sheet 小样（limit=4），开代理跑 VDE/UL，确认进度、标红、下载、原文件未改。
- 沿用既有 `tests/test_vde_parse.py`、`selftest.py`。

## Correctness Properties

### Property 1: 判断单一来源
Web 展示、M 列文案、Excel 标红三者必须由同一次 `compare_fields()` 结果派生；不存在第二套比对逻辑。
**Validates: Requirements 6.1, 7.3, 7.5**

### Property 2: 标红充要条件
单元格被标红 ⟺ 对应字段 `verdict == MISMATCH` 且该字段在 ColumnMap 有列映射。MISSING_SITE / MISSING_SHEET 及非 `found` 状态永不触发标红。
**Validates: Requirements 7.3, 7.4**

### Property 3: 原文件不可变
任一任务执行前后，源上传文件的字节内容保持不变（可用哈希断言）；所有写入只发生在副本。
**Validates: Requirements 7.1, 7.2**

### Property 4: UL 型号/标准语义
UL 结果中 model/standard 恒为空并按 MISSING_SITE 呈现，绝不因空值判 MISMATCH，也不用 CCN/文档名冒充型号。
**Validates: Requirements 6.5, 7.4**

### Property 5: 敏感数据零外泄
任何 API 响应与打包产物都不包含 `.ul_profile/`、`.cache/`、凭据。
**Validates: Requirements 8.3, 9.3, 10.3**

### Property 6: 取消/拦截保存不变式
任务被取消或因拦截提前停止时，已完成行的结果必定被写入输出文件。
**Validates: Requirements 4.4, 5.2**

### Property 7: 进度单调
`done` 不减，`done ≤ total`；`new_rows` 游标不重复、不遗漏。
**Validates: Requirements 4.1, 4.3**

## 安全设计小结（映射 req 9）

- 仅绑 `127.0.0.1`；无鉴权但不对外监听。
- 上传白名单 `.xlsx` + 大小上限；工作目录隔离在 `web/_work/`。
- 下载仅按 `task_id` 解析到该任务输出，路径规范化后校验前缀，拒绝穿越。
- `.ul_profile/`、`.cache/` 永不进入任何 API 响应或打包产物。
- 除对三家官网的必要核对请求外，不向第三方发送表格数据。
