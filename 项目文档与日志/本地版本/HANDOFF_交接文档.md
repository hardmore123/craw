# 交接文档 · 证书核对自动化工具（新 session 从这里接手）

> 一句话：把「安全件报备清单」的证书号逐条到 VDE / UL / TÜV 官网查询，比对制造商/型号/标准等，
> 输出两份沿用原表布局的 Excel（抓取表 + 对比表）。已做成本地网页前后端 + 命令行两种用法。

---

## 0. 立刻要知道的 5 件事

1. **Python 版本坑**：本机 PATH 的 `python` 指向 **Python 3.14**（没装 openpyxl、连 pip 都没有）。
   依赖装在 **Python 3.12**：`C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe`。
   **一切都用这个 3.12 解释器跑**。bat 里已有 `_py.bat` 自动挑 3.12。
2. **网络**：VDE/UL/TÜV 是境外站，公司网关直连被拦。必须开 VPN/加速器，本机代理
   `http://127.0.0.1:7877`（VDE、UL、TÜV **共用**这一个代理）。代理端口可能变，跑前先确认。
3. **UL 需要登录态**：`cert_verify/.ul_profile/`（Playwright 持久化 Edge）。已登录过。
   **不要打包/提交这个目录**。UL 用 `channel=msedge` 的真浏览器过 Cloudflare。
4. **查询间隔**：已按要求设为 **15 秒 + 0~5 秒随机抖动**（见 `http_client.py` 的
   `QUERY_MIN_INTERVAL/QUERY_JITTER` 和 `providers/ul.py` 的 `_throttle`）。
   => 全量很慢：CB+UL 约 250 行 ≈ **1 小时**。小样用 limit 参数。
5. **Excel 文件被 Excel 打开时无法覆盖**（会出现 `~$xxx.xlsx` 锁文件）。生成前先关 Excel。

---

## 1. 项目位置与关键文件

根目录：`c:\Users\likunyuan\Desktop\爬取项目\crawl4ai-main`

核心包 `cert_verify/`：
- `models.py` —— `CertRecord`：统一证书字段模型 + 状态常量（found/not_found/error/blocked/unsupported）。
- `http_client.py` —— 标准库 HTTP：**15s+抖动节流**、重试、磁盘缓存、代理解析、拦截页识别。
  常量 `QUERY_MIN_INTERVAL=15.0`、`QUERY_JITTER=5.0`。
- `compare.py` —— 归一化 + 字段比对。`compare_fields()` 出字段级结论；`summarize_fields()`
  组装 M 列文案（不一致行同时列出「哪些不一致」和「哪些一致」）；`compare_record()` 兼容旧签名。
- `verifier.py` —— **核对内核**（网页/命令行共用）：`VerifyEngine.iter_rows()` 惰性逐行、
  可取消、产出 `RowOutcome`（含字段级 verdict + 列定位）。
- `excel_writer.py` —— 两种写出：
  - `write_results()` = **对比表**（原值不动 + M列结论 + 不一致红字 + N列URL）。
  - `write_crawl_copy()` = **抓取表**（制造商/型号/标准列填官网值蓝字 + M列状态 + N列URL）。
- `config.py` —— `ColumnMap`（列映射）、`RunConfig`（运行参数：limit/proxy/row_delay/…）。
- `providers/`
  - `__init__.py` —— `ProviderRouter`：**路由规则见第 3 节**；含 `close()` 释放 UL 会话。
  - `tuv_rheinland.py` —— Certipedia，纯 HTTP。
  - `vde.py` —— VDE Solr REST 接口（免登录 JSON）。
  - `ul.py` —— UL Prospector，Playwright+Edge 登录态；`_throttle()` 15s；keyword `q:` + suggest→qm 回退。
  - `base.py` —— Provider 基类。
- `ul_login.py` —— UL 首次登录（弹 Edge，存 `.ul_profile`）。
- `web/` —— 本地网页前后端
  - `__main__.py` —— `python -m cert_verify.web`（默认 127.0.0.1:8756，占用自动 +1）。
  - `server.py` —— 标准库 HTTP 服务 + API（upload/run/progress/cancel/download/ul-status/ul-login）。
    `/api/download?id=&kind=diff|crawl` 分别下载对比表/抓取表。
  - `tasks.py` —— 任务管理：后台线程跑 `VerifyEngine`，结束同时产出对比表+抓取表两份文件。
  - `static/` —— `index.html` / `app.js` / `style.css`（上传→列映射→进度→结果与双下载）。
- `tests/` —— 离线测试（**不用联网，全绿**）：
  `test_routing.py`（路由/去前缀）、`test_compare_fields.py`、`test_excel_writer.py`、
  `test_web_flow.py`（网页全链路 monkeypatch）、`test_vde_parse.py`。
- `build_package.py` —— 打包脚本，产出 `本地Windows/交付/证书核对工具/` + zip。改代码后重跑它。

输入与产物 `本地Windows/交付/input/`：
- 输入报备清单（**文件名可能带日期后缀**，如 `…UL-CB 20230420 (1)---260810.xlsx`）。
- `导出两表.py` —— 一次抓取、同时产出「抓取结果导出.xlsx」+「对比结果导出.xlsx」；
  **自动定位输入文件 + 按表头自动识别列**（CB/UL/CB-UL 三表通吃）。
  用法：`python 导出两表.py [每表条数] [sheet1,sheet2,...]`（0=全部）。
- `run_test.py` —— 旧的小样测试脚本（单一列映射，历史遗留）。

本地 Windows 交付包位于：`本地Windows/交付/`；Linux 部署文件位于：`Linux/deploy/`。

规格文档：`.kiro/specs/cert-verify-web/`（requirements.md / design.md / tasks.md）。

---

## 2. 三张目标 sheet 的列布局（重要，各不相同）

| Sheet | 证书号 | 认证类别 | 型号 | 制造商 | 标准 | 数据起始行 |
|---|---|---|---|---|---|---|
| 认证类别CB | I | J | E | G | H | 2 |
| 认证类别UL | I | J | E | G | H | 2 |
| **认证类别CB-UL** | **J** | **K** | E | G | H | 2 |

- `导出两表.py` 已用**表头关键词自动识别**列，无需手填。
- 网页版列映射默认 I/M/G/E/H/J（适配 CB、UL）；**CB-UL 表要手动改证书号=J、机构列=K**
  （或后续给网页也加自动识别，见第 6 节 TODO）。

---

## 3. 路由规则（ProviderRouter.resolve，已按用户要求定稿）

优先级：
1. **证书号显式前缀最高**：`TUV …`→TÜV(Certipedia)、`VDE …`→VDE、`UL …`→UL；
   `ENEC …`→**暂不搜索**（标"人工核对"）。
2. 无前缀时按「认证类别」列：含 `UL`（含 CB/UL）→UL；纯 `CB`→VDE；含 VDE→VDE；含 TUV/莱茵→TÜV。
3. 都判不出→按证书号形态猜（VDE 排在 TÜV 前，避免 8 位数字冲突）。
- **不含数字的"证书号"**（如 CB-UL 里的 `Tested with Equipment`）→ 直接 `unsupported` 跳过，不查。
- 证书号去前缀：`UL E67006`→`E67006`；`VDE 40018250`→`40018250`。UL keyword 用 `EXXXXX`。

---

## 4. 常用命令（全部用 3.12 解释器）

设 `PY="C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"`

```powershell
# 离线测试（不联网）
& $PY cert_verify\tests\test_routing.py
& $PY cert_verify\tests\test_vde_parse.py

# 单条查询（需开 VPN + 代理）
$env:CERT_PROXY="http://127.0.0.1:7877"
& $PY -m cert_verify.cli query 40052271 --agency VDE
& $PY -m cert_verify.cli query E502081 --agency UL

# 生成两份表（自动找输入文件/自动识别列）；先关 Excel！
& $PY "本地Windows\交付\input\导出两表.py" 10                 # 小样每表10条
& $PY "本地Windows\交付\input\导出两表.py" 0                  # 全量（约1小时）

# 启动网页
& $PY -m cert_verify.web --port 8756

# 重新打包（改完代码）
& $PY cert_verify\build_package.py
```

---

## 5. 当前状态（已完成）

- 三站均实测可查（开代理 + UL 已登录）。
- 网页前后端可用：上传→列映射→实时进度→结果表→**下载对比表 / 抓取表**两份。
- 路由（前缀优先、CB/UL→UL、ENEC/Tested 跳过）、去前缀、15s+抖动间隔 均已落地并测过。
- 打包 v2.4，含 `一键启动.bat`（新电脑自动装依赖+启动）、`_py.bat`（自动选 3.12）、
  `迁移到新电脑说明.md`。
- 全量 CB(124)+UL(114) 曾成功跑通并导出（旧 1.2s 间隔时）；改 15s 后尚未重跑全量。

最近一次全量统计（供参考）：238 行，查到 229，官网无 0，被拦截 0；一致 180，需复核 58
（58 含 ENEC 9 跳过 + 真实型号差异 ~24 + TÜV 若干）。

---

## 6. 待办 / 下一步（TODO）

1. **用 15s 新间隔重跑全量三张表**（CB/UL/CB-UL），生成最终两份导出。约 1 小时；先关 Excel。
   `& $PY "本地Windows\交付\input\导出两表.py" 0`
2. 输出「待人工复核」清单（真实型号差异那 ~24 条 + 中英文混写的制造商）。
3. （可选）网页列映射**按表头自动识别**，省去 CB-UL 手动改 J/K。
4. （可选）VDE/UL「进详情页取标准/型号」，让标准、精确型号也能真正比对
   （目前 VDE 默认接口无标准、UL 列表页无型号，均记"网站无此项"）。
5. 迁移测试：在另一台电脑用 v2.4 包跑 `一键启动.bat` 验证。

---

## 7. 已知限制 / 易踩坑

- VDE 接口默认不返回「标准」；UL 列表页无「型号/标准」；官网普遍不公开「生产厂」
  → 这些记「网站无此项」，不判不一致。
- 中英文混写制造商（表格"东莞" vs 官网"Dongguan"）会判不一致，需人工看。
- PowerShell 用 `Set-Content` 改 .py 会破坏 UTF-8（中文变 GBK）→ 改代码用编辑器/str_replace，
  别用 `Set-Content` 重写含中文的源码。
- 终端偶发卡死；长任务建议后台跑或用 `*> 文件` 重定向后读文件看进度。
- UL 与网页服务不能同时抢 `.ul_profile`（Playwright 持久化目录独占）；跑 CLI 抓取前先停网页服务。

---

## 8. 给新 session 的第一步建议

1. 读本文件 + `.kiro/specs/cert-verify-web/design.md`。
2. 确认 VPN 开着、代理端口（`netstat` 看 7877 是否在听）。
3. 先跑离线测试确认代码完好：`& $PY cert_verify\tests\test_routing.py`。
4. 再按 TODO#1 用小样 `导出两表.py 10` 验证，OK 后全量。
