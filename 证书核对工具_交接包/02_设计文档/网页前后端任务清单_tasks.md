# Implementation Plan

## Overview

在现�?`cert_verify` 包上增量构建本地网页前后端。先把核对逻辑抽成可复用、可上报进度、可取消并产出字段级结论的内核（任务 1-5），再搭�?HTTP 服务�?API（任�?6-10），然后是前端页面与启动入口（任�?11-12），最后集成测试、打包与真实验证（任�?13-15）。所有改动不破坏既有比对语义、Provider 能力与「原文件不可变」约束�?

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2", "3"] },
    { "wave": 3, "tasks": ["4", "4.1"] },
    { "wave": 4, "tasks": ["5", "6"] },
    { "wave": 5, "tasks": ["7", "8", "9", "10"] },
    { "wave": 6, "tasks": ["11", "12"] },
    { "wave": 7, "tasks": ["13", "14"] },
    { "wave": 8, "tasks": ["15"] }
  ]
}
```

依赖说明�? �?2/3�? �?4 �?4.1 �?5�?+4 �?6�? �?7/8/9/10�?-10 �?11�?2 独立�?API�?1+12 �?13/14；全�?�?15�?

## Tasks

- [x] 1. �?`compare.py` 新增字段级比�?`compare_fields()`
  - 复用现有 `_CMP` 字段比较器，�?manufacturer/factory/model/standard/cert_status 逐字段产�?`(field, label, sheet_value, site_value, verdict, detail)`
  - verdict 取值沿�?`MATCH / MISMATCH / MISSING_SITE / MISSING_SHEET`
  - 重构 `compare_record` 使其基于 `compare_fields` 组装摘要文本，保持原返回签名 `(bool, str)` 与既有文案（含「官网正确值」）兼容
  - 处理�?`found` 状态（not_found/blocked/error/unsupported）：`compare_fields` 返回空字段列表，摘要�?`compare_record` 按原逻辑生成
  - _Requirements: 6.1, 7.3, 7.5_

- [x] 2. 新增核对内核 `cert_verify/verifier.py`
  - 定义 `FieldVerdict`、`RowOutcome` 数据类（见设计）
  - 实现 `VerifyEngine`：接�?`RunConfig` + �?sheet �?`ColumnMap`，用 `ProviderRouter` 逐行查询、`compare_fields` 比对，`iter_rows()` 惰性产�?`RowOutcome`
  - `RowOutcome.verdict_text` �?MISMATCH 字段附官网正确值（�?`�?型号不一致；官网：A24-065N3A`�?
  - 提供字段→列字母定位信息，供 excel_writer 标红
  - _Requirements: 3.6, 4.1, 4.3, 6.1_

- [x] 3. �?`compare_fields` 写单元测�?
  - 用例：UL 型号空→MISSING_SITE；VDE `WMR10D112K *` 通配→MATCH；标准年份差异→MATCH+注记；真实型号不同→MISMATCH；表格空字段→MISSING_SHEET
  - 断言 `compare_record` 文案与重构前一致（回归�?
  - _Requirements: 6.1, 6.5, 7.4_

- [x] 4. 新增 Excel 写入�?`cert_verify/excel_writer.py`
  - `write_results(src, out, outcomes_by_sheet, colmap_by_sheet, detail_col)`：load 原文件副本、另�?out，绝不写�?src
  - M 列写 `verdict_text`；detail_col �?`source_url or message`
  - �?MISMATCH 且有列映射的字段单元格设**红色字体**：复制原 `cell.font` 仅改 `color="FFFF0000"`，保留字�?加粗/字体名，不动填充与边�?
  - MISSING_SITE/MISSING_SHEET/�?found 状态不改字段样�?
  - 降级：保存异常时退回「仅写值不设样式」再存，返回降级标记
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.8_

- [x] 4.1 �?`excel_writer` 写单元测�?
  - 造小�?xlsx：断言�?MISMATCH 字段字体变红、原字体属性保留；MISSING/未查到不�?
  - 断言源文件字节哈希在写入前后不变（Property 3�?
  - _Requirements: 7.1, 7.3, 7.4_

- [x] 5. 重构 `excel_runner.run()` 基于 `VerifyEngine` + `excel_writer`
  - �?`VerifyEngine.iter_rows()` 替换内联逐行逻辑，保�?CLI 输出行为、`stop_on_blocked`、summarize 不变
  - 改用 `excel_writer.write_results` 产出（获得标红能力），验证既�?`run_test.py`/`selftest` 仍通过
  - _Requirements: 3.4, 4.4_

- [x] 6. 新增 Web 服务骨架 `cert_verify/web/server.py` + TaskManager
  - `ThreadingHTTPServer` 仅绑 `127.0.0.1`；`BaseHTTPRequestHandler` �?path 分发
  - `TaskManager` + `Task` 内存任务表（状�?进度/统计/取消 Event/输出路径/错误/停止原因�?
  - 后台线程运行 `VerifyEngine.iter_rows()`，逐行更新进度、检查取消、遇 BLOCKED 处理 stop_on_blocked，结束调�?excel_writer 落盘
  - _Requirements: 1.4, 3.6, 4.4, 4.5, 5.2, 5.3_

- [x] 7. 实现 `/api/upload`
  - 只接�?`.xlsx`、限制大小（�?0MB）；保存�?`web/_work/<file_id>/`
  - 解析 sheet 列表 + �?sheet 前若干行预览返回（复�?`inspect_workbook` 逻辑�?
  - 非法/超限返回中文错误、不建任�?
  - _Requirements: 2.1, 2.2, 2.6, 9.1_

- [x] 8. 实现 `/api/run`、`/api/progress`、`/api/cancel`
  - run：接收每 sheet �?colmap+enabled、limit、proxy、default_agency、stop_on_blocked、detail_col，创建任务返�?task_id
  - progress：返�?status/total/done/cur_*/stats + 增量 `new_rows`（游�?offset，不重复不遗漏）
  - cancel：置取消标志，保存已完成部分，状态转 stopped；已完成任务安全忽略
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3, 4.6, 5.1, 5.4_

- [x] 9. 实现 `/api/ul-status`、`/api/ul-login`
  - ul-status：检�?`.ul_profile/` 判断 logged_in，返�?login_running
  - ul-login：后台线程调�?`ul_login.login()` 弹本�?Edge；完成后重检登录�?
  - 绝不在任何响应返�?`.ul_profile` 内容/Cookie
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

- [x] 10. 实现 `/api/download` + 路径安全
  - 仅按合法 task_id 映射�?`_work/<id>/` 内输出文件，路径规范化后校验前缀，拒绝穿�?
  - 以附件流返回 xlsx
  - _Requirements: 7.7, 9.2, 9.4_

- [x] 11. 前端单页 `cert_verify/web/static/`（index.html/app.js/style.css�?
  - 上传�?�?列映射区（每 sheet 表单 + 套用全部 + 运行参数 + UL 登录按钮/状态灯）→ 进度区（进度�?当前�?统计/取消）→ 结果区（结果�?状态色/筛�?行明�?下载�?
  - 标红字段在明细中显式展示表格�?vs 官网值；UL 型号/标准显示「官网未提供�?
  - 轮询 progress，断线后�?task_id 恢复
  - _Requirements: 2.3, 2.4, 2.5, 2.7, 4.1, 4.2, 5.1, 6.1, 6.2, 6.3, 6.4, 6.5, 7.7, 8.1_

- [x] 12. 启动入口 `cert_verify/web/__main__.py` + 端口选择
  - `python -m cert_verify.web` 启动；默�?`127.0.0.1:8756`，占用则 +1 递增
  - 打印实际 URL �?`webbrowser.open`
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 13. 集成测试（离线，monkeypatch�?
  - monkeypatch `ProviderRouter.query` 返回预置 CertRecord，跑 upload→run→progress→download 全链�?
  - 断言进度单调、new_rows 无重叠、下载文件存在且源文件未改、标红正�?
  - download 路径穿越、upload 类型校验的针对性测�?
  - _Requirements: 4.3, 7.1, 9.2, 9.4_

- [x] 14. 打包与文档更�?
  - `build_package.py`：CODE_FILES/目录纳入 `web/`（server、__main__、static/*）；新增 `启动网页.bat`；更�?FILE_NOTES/文件清单；确认排�?`.ul_profile/`、`.cache/`、`__pycache__/`、测试输�?
  - 更新 `PACKAGE_README.md` / 介绍文档：网页用法、标红含义、VDE 代理/UL 登录现状
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [ ] 15. 真实小样手动验证
  - �?`dist/input/...xlsx` 两个 sheet，limit=4，开代理跑：确认进度实时、不一致红字、下载副本、原文件未改、UL 型号显示「官网未提供�?
  - _Requirements: 4.1, 6.2, 7.1, 7.3_

## Notes

- 后端零额外依赖（标准�?`http.server`），�?`openpyxl`（已有）必需，`playwright` �?UL 需要�?
- 判断只有一套：Web 展示、M 列文案、Excel 标红都来自同一�?`compare_fields()`�?
- 标红用红色字体（`FFFF0000`），保留原字体其他属性，不改填充/边框�?
- 原上传文件保持只读，所有写入落在副本与 `web/_work/` 工作目录�?
- 敏感数据（`.ul_profile/`、`.cache/`、凭据）不进入任�?API 响应与打包产物�?
- 联网相关（VDE 需代理、UL 需登录）在测试中用 monkeypatch 离线覆盖，真实联网仅在任�?15 手动验证�?
