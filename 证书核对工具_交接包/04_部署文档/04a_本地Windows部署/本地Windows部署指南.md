# 本地 Windows 部署指南

> 本文件专门讲在 **Windows 本机** 如何部署和使用证书核对工具。服务器部署见同级的 `04b_服务器Linux部署/`。

---

## 一、环境准备

### 1. Python 版本（关键坑）

本机 PATH 的 `python` 可能指向 **Python 3.14**（没装 openpyxl，连 pip 都没有）。依赖装在 **Python 3.12**：

```
C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe
```

**一切都用这个 3.12 跑**。交付包里的 `_py.bat` 会自动挑 3.12。

验证：
```powershell
py -3.12 --version    # 期望 Python 3.12.x
py -3.12 -c "import openpyxl; print('ok')"   # 期望 ok
```

### 2. 装依赖

```powershell
py -3.12 -m pip install -r cert_verify\requirements.txt
# 要查 UL 再装（TÜV/VDE 不需要）：
py -3.12 -m pip install playwright
```

依赖清单（`requirements.txt`）：
- `openpyxl==3.1.5`（必需，读写 xlsx）
- `playwright`（可选，仅 UL 需要）

### 3. 网络（很重要）

VDE / UL / TÜV 是**境外站**，公司网关直连被拦。

- **必须开 VPN/加速器**，让本机有一个能出网的代理（如 `http://127.0.0.1:7877`）
- 工具会**自动探测系统代理**，无需额外配置
- 也可显式指定：`--proxy http://127.0.0.1:7877` 或设环境变量 `set CERT_PROXY=http://127.0.0.1:7877`
- **三家共用同一个代理**（VDE、UL、TÜV 都走 127.0.0.1:7877）
- 代理端口可能变，跑前先确认：`netstat -ano | findstr 7877`

| 机构 | 需要代理 | 需要登录 |
|---|---|---|
| TÜV Rheinland | 否（直连即可） | 否 |
| VDE | **是** | 否 |
| UL Prospector | **是** | **是**（首次登录） |

---

## 二、两种部署方式

### 方式 A：用交付包（推荐，解压即用）

1. 进入 `crawl4ai-main/本地Windows/交付/证书核对工具/`
2. 双击 **`一键启动.bat`**（新电脑首次用：自动查 Python → 装依赖 → 启动网页）
   - 或双击 **`启动网页.bat`**（依赖已装好时用）
3. 浏览器自动打开 `http://127.0.0.1:8756`
4. ★ 使用期间**不要关闭那个黑色命令行窗口**（它就是后台服务）

交付包内的 bat 脚本（都在 `04a_本地Windows部署/` 下有副本）：

| bat | 用途 |
|---|---|
| `一键启动.bat` | ★★ 新电脑首次用：自动装依赖+启动网页 |
| `启动网页.bat` | ★ 启动网页版（依赖已装好时用） |
| `1_安装依赖.bat` | 装 openpyxl（只需跑一次） |
| `2_网络自检.bat` | 各机构网站可达性 + 分层诊断 |
| `3_查看表格列结构.bat` | 把 xlsx 拖到图标上，打印各 sheet 表头 |
| `4_试跑10条.bat` | 只核对前 10 条，验证列映射 |
| `5_全量核对.bat` | 全量核对，结果写 M 列 |
| `6_UL登录（首次）.bat` | UL 首次登录（弹 Edge 人工登一次） |
| `9_自测（验证程序本身）.bat` | 造样例表→真实抓 TÜV→比对→写回 |
| `0_看介绍文档.bat` | 打开介绍文档 HTML |

### 方式 B：从源码开发

```powershell
$PY = "C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"
cd "C:\Users\likunyuan\Desktop\爬取项目\crawl4ai-main"

# 跑离线测试（不联网，确认代码完好）
& $PY cert_verify\tests\test_routing.py
& $PY cert_verify\tests\test_vde_parse.py
& $PY cert_verify\tests\test_compare_fields.py
& $PY cert_verify\tests\test_excel_writer.py
& $PY cert_verify\tests\test_web_flow.py

# 单条查询（需开 VPN + 代理）
$env:CERT_PROXY="http://127.0.0.1:7877"
& $PY -m cert_verify.cli query 50679826 --agency TUV    # TÜV 直连
& $PY -m cert_verify.cli query 40052271 --agency VDE    # VDE 需代理
& $PY -m cert_verify.cli query E502081 --agency UL       # UL 需代理+登录

# 启动网页
& $PY -m cert_verify.web --port 8756

# 命令行全量核对
& $PY -m cert_verify.cli run "清单.xlsx" --limit 10    # 小样
& $PY -m cert_verify.cli run "清单.xlsx"               # 全量

# 用导出两表脚本（自动找输入文件+自动识别列，一次产出两份表）
& $PY "本地Windows\交付\input\导出两表.py" 10           # 小样每表10条
& $PY "本地Windows\交付\input\导出两表.py" 0            # 全量（约1小时）

# 重新打包（改完代码）
& $PY cert_verify\build_package.py
```

---

## 三、UL 首次登录（仅 UL 需要）

UL Prospector 挡在 Cloudflare 后面，脚本直连 403。只有真浏览器能过。

1. 确认已装 Playwright：`py -3.12 -m pip install playwright`
2. 运行 `py -3.12 -m cert_verify.ul_login`（或双击 `6_UL登录（首次）.bat`）
3. 弹出 Edge 浏览器，在里面**手动登录** UL Prospector
4. 登录态存到 `cert_verify/.ul_profile/`，之后抓取复用
5. **每台电脑各登一次**，登录态不可跨平台复制

> **注意**：`.ul_profile/` 含敏感 Cookie，**不要打包、不要提交 Git、不要复制到 Linux**。
> UL 与网页服务不能同时抢 `.ul_profile`（Playwright 持久化目录独占）；跑 CLI 抓取前先停网页服务。

---

## 四、三张目标 sheet 的列布局（重要，各不相同）

| Sheet | 证书号 | 认证类别 | 型号 | 制造商 | 标准 | 数据起始行 |
|---|---|---|---|---|---|---|
| 认证类别CB | I | J | E | G | H | 2 |
| 认证类别UL | I | J | E | G | H | 2 |
| **认证类别CB-UL** | **J** | **K** | E | G | H | 2 |

- 网页版 autoDetect 已能**按表头关键词自动识别**列（连 CB-UL 表也能认出）
- 命令行版用 `导出两表.py` 也会自动识别列

---

## 五、本地部署检查清单

| 检查项 | 命令 | 期望结果 |
|---|---|---|
| Python 版本 | `py -3.12 --version` | Python 3.12.x |
| openpyxl | `py -3.12 -c "import openpyxl"` | 无报错 |
| 离线测试 | `py -3.12 cert_verify\tests\test_routing.py` | 全部通过 |
| 代理端口 | `netstat -ano \| findstr 7877` | 有监听（VPN 开着时） |
| 网络自检 | `py -3.12 -m cert_verify.cli netcheck` | TÜV 可达 |
| TÜV 单条 | `py -3.12 -m cert_verify.cli query 50679826 --agency TUV` | found |
| 网页启动 | `py -3.12 -m cert_verify.web --port 8756` | 浏览器打开 |

---

## 六、本地易踩坑

| 坑 | 对策 |
|---|---|
| Python 3.14 没装库 | 用 3.12，或用 `_py.bat` 自动挑 |
| Excel 被打开时无法覆盖（`~$xxx.xlsx` 锁文件） | 生成前先关 Excel |
| PowerShell `Set-Content` 改 .py 破坏 UTF-8（中文变 GBK） | 用编辑器/edit，别用 Set-Content |
| iGG 浏览器扩展代理只代理浏览器内流量，脚本走不了 | 需开挂系统代理的 VPN/加速器 |
| UL 一直转/被拦截 | 登录态失效，重新点「UL 登录」登一次 |
| VDE/UL 查不到、超时 | 没开 VPN，或代理端口填错 |
