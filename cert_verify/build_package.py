"""把「能跑通的部分」打包成可直接交付的独立目录 + zip。

只打包已验证可用的链路（TÜV Rheinland Certipedia 抓取 + Excel 比对写回），
VDE / UL 的适配器一并带上但在说明里标明「等网络放通」。

    python cert_verify\\build_package.py
产出：
    本地Windows/交付/证书核对工具/            解压即用的目录
    本地Windows/交付/证书核对工具_v2.7.zip    交付用压缩包
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = os.path.dirname(os.path.abspath(__file__))                 # ...\cert_verify
ROOT = os.path.dirname(SRC)                                       # ...\crawl4ai-main
LOCAL_ROOT = os.path.join(ROOT, "本地Windows")
DIST = os.path.join(LOCAL_ROOT, "交付")
PKG_NAME = "证书核对工具"
PKG = os.path.join(DIST, PKG_NAME)
VERSION = "2.7"

# 打进包里的源码文件
CODE_FILES = [
    "__init__.py", "cli.py", "compare.py", "config.py", "decrypt.py", "excel_runner.py",
    "excel_writer.py", "verifier.py",
    "http_client.py", "browser_client.py", "models.py", "netcheck.py", "netdiag.py",
    "selftest.py", "render_doc.py", "ul_login.py", "介绍文档.Rmd", "requirements.txt",
]
PROVIDER_FILES = ["__init__.py", "base.py", "tuv_rheinland.py", "vde.py", "ul.py"]
# 网页前后端：web/ 包 + static 静态资源
WEB_FILES = ["__init__.py", "__main__.py", "server.py", "tasks.py"]
WEB_STATIC_FILES = ["index.html", "app.js", "style.css"]
TEST_FILES = ["test_vde_parse.py", "test_compare_fields.py", "test_excel_writer.py",
              "test_web_flow.py", "test_routing.py"]
FIXTURE_FILES = ["vde_40052271.json"]

# 额外带上的文件：(源路径相对 cert_verify, 包内目标相对路径)
EXTRA_FILES = [
    ("README.md", "开发说明.md"),
    ("build_package.py", "cert_verify/build_package.py"),
    ("netdiag_report.txt", "网络诊断快照.txt"),
    (os.path.join("..", "Linux", "deploy", "certverify.service"), "deploy/certverify.service"),
]

# 逐文件备注 —— 生成「文件清单.md」用。键是包内文件名（不含目录）
FILE_NOTES = {
    # 入口批处理
    "_py.bat": "内部脚本：自动挑选带依赖的 Python（避开没装库的新版本），其它 bat 会调用它",
    "一键启动.bat": "★★ 新电脑首次用：自动查Python→装依赖→启动网页（推荐）",
    "启动网页.bat": "★ 启动网页版（依赖已装好时用）：上传→选列→核对→标红下载",
    "0_看介绍文档.bat": "打开介绍文档 HTML；没有就先渲染再打开",
    "1_安装依赖.bat": "装 openpyxl（只需跑一次）",
    "2_网络自检.bat": "① 各机构网站可达性 ② 分层诊断（浏览器能上、脚本不能时看这个）",
    "3_查看表格列结构.bat": "把 xlsx 拖到图标上，打印各 sheet 表头，用于配列映射",
    "4_试跑10条.bat": "只核对前 10 条，验证列映射配对了没",
    "5_全量核对.bat": "全量核对，结果写 M 列，另存 *_核对结果.xlsx（不改原文件）",
    "6_UL登录（首次）.bat": "UL 首次登录（弹 Edge 人工登一次，存登录态）；需先装 Playwright",
    "9_自测（验证程序本身）.bat": "造样例表→真实抓 TÜV→比对→写回，外加 VDE 解析器 20 项断言",
    # 文档
    "使用说明.md": "★ 主文档：现状、操作步骤、结果解读、已知限制",
    "迁移到新电脑说明.md": "★★ 换电脑测试看这个：装Python→一键启动→填代理→UL登录",
    "介绍文档.html": "同内容的网页版，带目录，双击即看",
    "开发说明.md": "开发向说明：接口细节、字段映射、抓取路径考证",
    "文件清单.md": "★ 本清单（自动生成）：每个文件干什么，一眼看全",
    "网络现状快照.txt": "打包时的 netcheck 结果：哪几家能连",
    "网络诊断快照.txt": "打包时的 netdiag 结果：不能连的卡在哪一层",
    os.path.join("deploy", "certverify.service"): "Linux systemd 服务文件：固定 venv、工作目录、解密服务和 8756 端口",
    # 核心代码
    "cli.py": "命令行入口：netcheck / netdiag / query / inspect / run / probe-vde / parse-file",
    "config.py": "★ 列映射配置 —— 唯一需要你手改的文件",
    "decrypt.py": "透明加密文件解密兜底：检测密文并调用内网解密服务还原 xlsx",
    "excel_runner.py": "命令行核对入口：读 xlsx → 逐行查询 → 比对 → 写 M 列（基于内核）",
    "verifier.py": "核对内核（网页/命令行共用）：惰性逐行、可取消、产出字段级结论",
    "excel_writer.py": "结果写入层：原文件副本上写结论 + 真不一致字段红字标注",
    "compare.py": "字段归一化与五项比对；公司后缀、型号通配、标准年份都在这里处理",
    "models.py": "CertRecord：各机构字段统一映射到这个模型，比对层不关心数据来源",
    "http_client.py": "HTTP 客户端：重试 / 查询间隔(网页可设,默认1s+抖动) / 磁盘缓存 / 识别防火墙拦截页",
    "browser_client.py": "Playwright 封装，供需要浏览器渲染的站点用（可选依赖）",
    "netcheck.py": "各机构网站可达性检查",
    "netdiag.py": "分层网络诊断：DNS→TCP→TLS→出口，定位为什么脚本连不上",
    "selftest.py": "端到端自测：造表、真实抓取、比对、写回",
    "ul_login.py": "UL 首次登录：弹 Edge 人工登一次，存登录态供复用",
    "render_doc.py": "把 介绍文档.Rmd 渲染成 HTML（优先 R，其次 pandoc）",
    "介绍文档.Rmd": "介绍文档源文件（R Markdown）",
    "requirements.txt": "依赖清单",
    "build_package.py": "打包脚本：改完代码跑一次即重新生成 dist 产物",
    "__init__.py": "包标记",
    # providers
    os.path.join("cert_verify", "providers", "__init__.py"):
        "ProviderRouter：按「认证机构」列或证书号形态决定用哪个适配器",
    os.path.join("cert_verify", "__init__.py"): "包标记",
    "base.py": "Provider 基类 + HTML 取文本工具",
    "tuv_rheinland.py": "★ TÜV Rheinland（Certipedia）—— 已验证可用，纯 HTTP",
    "vde.py": "★ VDE —— 直连官方 restSearch JSON 接口，解析已对真实响应验证，等网络放通",
    "ul.py": "★ UL Prospector —— Playwright 登录态 + suggest→qm 精确查询，已实测打通",
    # web 前后端
    os.path.join("cert_verify", "web", "__init__.py"): "网页包标记",
    "__main__.py": "网页启动入口：python -m cert_verify.web",
    "server.py": "本地 HTTP 服务 + JSON API（默认 127.0.0.1，可用 --host 绑定服务器地址）",
    os.path.join("cert_verify", "web", "tasks.py"): "任务管理：内存任务表 + 后台核对线程 + 进度快照",
    "index.html": "网页界面：上传 / 列映射 / 进度 / 结果",
    "app.js": "前端逻辑：上传、轮询进度、结果表、行明细、下载",
    "style.css": "网页样式",
    # 测试
    "test_vde_parse.py": "VDE 解析器离线测试，20 项断言，不需要联网",
    "test_compare_fields.py": "字段级比对离线测试（标红判定的核心）",
    "test_excel_writer.py": "标红写入 + 原文件不可变离线测试",
    "test_web_flow.py": "网页全链路离线集成测试（monkeypatch，不联网）",
    "test_routing.py": "路由规则测试：认证类别→网站、证书号去前缀（离线）",
    "vde_40052271.json": "证书号 40052271 的真实接口响应，作为测试基准",
}

DIR_NOTES = {
    ".": "入口与文档",
    "cert_verify": "程序主体",
    "deploy": "Linux systemd 部署文件",
    os.path.join("cert_verify", "providers"): "各认证机构的适配器，一家一个文件",
    os.path.join("cert_verify", "web"): "网页前后端：本地服务 + API + 任务管理",
    os.path.join("cert_verify", "web", "static"): "网页静态资源（HTML/JS/CSS）",
    os.path.join("cert_verify", "tests"): "测试",
    os.path.join("cert_verify", "tests", "fixtures"): "测试基准数据（真实接口响应）",
}

BATS = {
    "1_安装依赖.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 安装依赖（只需跑一次）===
python -m pip install -r cert_verify\requirements.txt
echo.
echo 如果后面要抓 VDE / UL（需要浏览器渲染），再执行：
echo     python -m pip install playwright
echo     python -m playwright install chromium
echo.
pause
""",
    "2_网络自检.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 检查各认证机构网站能不能连上（抓取前必跑）===
python -m cert_verify.cli netcheck
echo.
echo === 分层诊断：如果浏览器能打开但脚本不行，看这里 ===
python -m cert_verify.cli netdiag
echo.
pause
""",
    "3_查看表格列结构.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "XLSX=%~1"
if "%XLSX%"=="" set /p XLSX=把 xlsx 拖到本窗口后回车（或直接把文件拖到本 bat 图标上）:
python -m cert_verify.cli inspect "%XLSX%"
echo.
echo 把上面打印的列字母填到 cert_verify\config.py 的 SHEET_COLUMNS 里，再跑第 4 步。
echo.
pause
""",
    "4_试跑10条.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "XLSX=%~1"
if "%XLSX%"=="" set /p XLSX=把 xlsx 拖到本窗口后回车:
echo === 只跑前 10 条，先确认结果对不对 ===
python -m cert_verify.cli run "%XLSX%" --limit 10 --detail-col N
echo.
pause
""",
    "5_全量核对.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "XLSX=%~1"
if "%XLSX%"=="" set /p XLSX=把 xlsx 拖到本窗口后回车:
echo === 全量核对，结果写入 M 列，另存为 *_核对结果.xlsx（不改原文件）===
echo （VDE/IECEE 需先开 VPN/加速器；工具会自动走系统代理）
python -m cert_verify.cli run "%XLSX%" --detail-col N
echo.
pause
""",
    "9_自测（验证程序本身）.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 造样例表 + 真实抓取 TUV + 比对 + 写回，验证程序可用 ===
python -m cert_verify.selftest
echo.
echo === VDE 解析器单测（不联网）===
python cert_verify\tests\test_vde_parse.py
echo.
pause
""",
    "6_UL登录（首次）.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === UL Prospector 首次登录（需已装 Playwright）===
echo 会弹出 Edge 窗口，请在里面登录 UL，脚本自动检测并存登录态。
echo 没装 Playwright 就先跑： python -m pip install playwright
python -m cert_verify.ul_login
echo.
pause
""",
    "启动网页.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   证书核对 · 网页版
echo   稍等几秒，浏览器会自动打开 http://127.0.0.1:8756
echo.
echo   ★★ 本窗口是后台服务，使用期间请【不要关闭】这个黑窗口！
echo      关掉它网页就打不开了。用完再关。
echo.
echo   查 VDE 需先开 VPN/加速器；查 UL 在网页上点「UL 登录」先登一次。
echo ============================================================
echo.
python -m cert_verify.web
echo.
echo 服务已结束。按任意键关闭本窗口。
pause >nul
""",
    "一键启动.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   证书核对工具 · 一键启动（新电脑首次用这个）
echo ============================================================
echo.
echo [1/3] 检查 Python ...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [x] 未检测到 Python。请先安装 Python 3.10+，安装时务必勾选
    echo       "Add python.exe to PATH"，装完重开本窗口再双击本脚本。
    echo       下载地址： https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
python --version
echo.
echo [2/3] 安装依赖（首次需要联网，之后会跳过）...
python -m pip install -r cert_verify\requirements.txt
echo   - 安装 Playwright（查 UL 需要；只查 VDE/TUV 可忽略其报错）...
python -m pip install playwright
echo.
echo [3/3] 启动网页服务 ...
echo   浏览器会自动打开 http://127.0.0.1:8756
echo   ★★ 使用期间请【不要关闭】本窗口！用完再关。
echo   （查 VDE/UL 需先开 VPN/加速器；查 UL 首次在网页点「UL 登录」登一次）
echo ============================================================
echo.
python -m cert_verify.web
echo.
echo 服务已结束。按任意键关闭本窗口。
pause >nul
""",
    "0_看介绍文档.bat": r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "介绍文档.html" (
    echo 打开已生成的 介绍文档.html
    start "" "介绍文档.html"
    goto :eof
)
echo === 渲染介绍文档（有 R 用 rmarkdown，否则用 pandoc）===
python -m cert_verify.render_doc
if exist "介绍文档.html" (
    start "" "介绍文档.html"
) else (
    echo.
    echo 没能生成 HTML。直接用记事本或 VS Code 打开 cert_verify\介绍文档.Rmd 也能看。
    pause
)
""",
}


def copy_code() -> None:
    if os.path.exists(PKG):
        shutil.rmtree(PKG)
    os.makedirs(os.path.join(PKG, "cert_verify", "providers"), exist_ok=True)
    os.makedirs(os.path.join(PKG, "cert_verify", "tests", "fixtures"), exist_ok=True)
    os.makedirs(os.path.join(PKG, "cert_verify", "web", "static"), exist_ok=True)

    for f in CODE_FILES:
        shutil.copy2(os.path.join(SRC, f), os.path.join(PKG, "cert_verify", f))
    for f in PROVIDER_FILES:
        shutil.copy2(os.path.join(SRC, "providers", f),
                     os.path.join(PKG, "cert_verify", "providers", f))
    for f in WEB_FILES:
        shutil.copy2(os.path.join(SRC, "web", f),
                     os.path.join(PKG, "cert_verify", "web", f))
    for f in WEB_STATIC_FILES:
        shutil.copy2(os.path.join(SRC, "web", "static", f),
                     os.path.join(PKG, "cert_verify", "web", "static", f))
    for f in TEST_FILES:
        shutil.copy2(os.path.join(SRC, "tests", f),
                     os.path.join(PKG, "cert_verify", "tests", f))
    for f in FIXTURE_FILES:
        shutil.copy2(os.path.join(SRC, "tests", "fixtures", f),
                     os.path.join(PKG, "cert_verify", "tests", "fixtures", f))

    # 网络自检快照，交付时对方一看就知道现在哪能抓
    snap = os.path.join(SRC, "netcheck_report.txt")
    if os.path.exists(snap):
        shutil.copy2(snap, os.path.join(PKG, "网络现状快照.txt"))

    for src_rel, dst_rel in EXTRA_FILES:
        src = os.path.join(SRC, src_rel)
        if not os.path.exists(src):
            print(f"[warn] 缺少 {src_rel}，跳过")
            continue
        dst = os.path.join(PKG, dst_rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


# 选 Python 解释器：优先带 openpyxl 依赖的（3.12/3.11/3.10/3.13），
# 找不到再退而求其次找带 pip 的（供首次安装依赖），最后退回 python。
# 目的：避免 PATH 里的 python 指向没装依赖、甚至没 pip 的新版本（如 3.14）。
PY_FINDER = r"""@echo off
set "PYEXE="
for %%V in (3.12 3.11 3.10 3.13) do (
    if not defined PYEXE ( py -%%V -c "import openpyxl" >nul 2>&1 && set "PYEXE=py -%%V" )
)
if not defined PYEXE ( python -c "import openpyxl" >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE (
    for %%V in (3.12 3.11 3.10 3.13) do (
        if not defined PYEXE ( py -%%V -m pip --version >nul 2>&1 && set "PYEXE=py -%%V" )
    )
)
if not defined PYEXE ( python -m pip --version >nul 2>&1 && set "PYEXE=python" )
if not defined PYEXE ( py -c "import sys" >nul 2>&1 && set "PYEXE=py" )
if not defined PYEXE ( python -c "import sys" >nul 2>&1 && set "PYEXE=python" )
"""


def write_bats() -> None:
    """写入 bat，并在开头插一段 REM 注释说明这个脚本干什么、怎么用。

    另外：写入 _py.bat（探测可用 Python），并让每个 bat 先 call 它、
    用 %PYEXE% 代替裸 python，避免命中没装依赖的解释器。
    """
    with open(os.path.join(PKG, "_py.bat"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(PY_FINDER)

    order = sorted(BATS)          # 按文件名排序，注释里的执行顺序才对
    for name, body in BATS.items():
        # 用探测到的解释器替换裸 python 调用
        body = body.replace("python -m ", "%PYEXE% -m ").replace(
            "python cert_verify", "%PYEXE% cert_verify").replace(
            "python -", "%PYEXE% -")
        # 在 cd 之后插入 call _py.bat
        body = body.replace('cd /d "%~dp0"\n',
                            'cd /d "%~dp0"\ncall "%~dp0_py.bat"\n', 1)
        note = FILE_NOTES.get(name, "")
        header = [
            f"REM ============================================================",
            f"REM {name}",
            f"REM 作用: {note or '(见 使用说明.md)'}",
        ]
        if "%~1" in body:
            header.append("REM 用法: 把 xlsx 文件拖到本 bat 的图标上；或双击后粘贴路径")
        else:
            header.append("REM 用法: 直接双击")
        header += [
            f"REM 顺序: 按文件名数字从小到大跑 —— {' → '.join(order)}",
            "REM 说明文档: 使用说明.md / 文件清单.md",
            "REM ============================================================",
            "",
        ]
        text = body.replace("@echo off\n", "@echo off\n" + "\n".join(header), 1)
        with open(os.path.join(PKG, name), "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text)


def write_readme() -> None:
    shutil.copy2(os.path.join(SRC, "PACKAGE_README.md"), os.path.join(PKG, "使用说明.md"))


MIGRATION_GUIDE = """# 迁移到新电脑 · 一键测试说明

把本文件夹整个拷到新电脑任意位置（桌面即可），按下面做：

## 一、准备（新电脑只需一次）
1. **装 Python 3.10+**：https://www.python.org/downloads/
   安装时**务必勾选** “Add python.exe to PATH”。
2. 双击 **`一键启动.bat`**：它会自动装依赖（openpyxl、playwright）并启动网页。
   - 首次装依赖需要联网，之后会自动跳过。
   - 浏览器会自动打开 `http://127.0.0.1:8756`。
   - ★ 使用期间**不要关闭那个黑色命令行窗口**（它就是后台服务）。

## 二、网络前提（很重要）
- 本工具查的是**境外官网**（VDE / UL / TÜV），公司内网直连会被网关拦。
- **新电脑同样需要开 VPN/加速器**，让本机有一个能出网的代理（如 `127.0.0.1:7877`）。
- 在网页的「代理」框填 `http://127.0.0.1:7877`（端口按你机器上加速器的实际端口）。
  - VDE 和 UL **共用**这个代理。
  - 怎么确认端口：加速器/VPN 客户端设置里的“本地端口/HTTP代理端口”。

## 三、查 UL 需要先登录（每台电脑各登一次）
- 在网页点 **「UL 登录」** 按钮，会弹出本机 Edge，在里面登录 UL Prospector。
- 登录态存在本机 `cert_verify\\.ul_profile\\`，**不随本包分发**（新电脑要自己登一次）。
- 只查 VDE/TÜV 的话，不需要 UL 登录。

## 四、开始用
1. 网页上「上传报备清单」选你的 xlsx。
2. 列映射默认已按本清单填好：证书号 I、结果 M、制造商 G、型号 E、标准 H、机构列 J、起始行 2。
   （「认证类别CB-UL」表列不同：证书号改 J、机构列改 K）
3. 填代理 → 勾选要核对的工作表 → 「开始核对」。
4. 结果表点行看字段明细；「下载结果 Excel」得到副本（真不一致字段红字，原文件不改）。

## 五、路由规则（自动）
- 证书号写了 `TUV …`→查 TÜV(Certipedia)，`VDE …`→查 VDE，`UL …`→查 UL；
  没写前缀时按「认证类别」列：CB→VDE，UL/CB/UL→UL。
- `ENEC …` 暂不自动核对，标为“请人工核对”。

## 六、命令行自测（可选，验证程序本身没问题）
```
python cert_verify\\tests\\test_routing.py
python cert_verify\\tests\\test_vde_parse.py
```
不联网也能通过，说明代码搬过来是完好的。

## 常见问题
- **双击 一键启动.bat 一闪就没了**：多半是没装 Python 或没勾 PATH。装好后重开窗口再试。
- **网页打不开/拒绝连接**：黑窗口被关了，或端口被占用（会自动换 8757…，看窗口里打印的地址）。
- **VDE/UL 查不到、超时**：没开 VPN，或代理端口填错。先开加速器，再核对端口。
- **UL 一直转/被拦截**：登录态失效，重新点「UL 登录」登一次。
"""


def write_migration_guide() -> None:
    with open(os.path.join(PKG, "迁移到新电脑说明.md"), "w", encoding="utf-8") as f:
        f.write(MIGRATION_GUIDE)


JUNK_DIRS = {"__pycache__", ".cache", "_selftest", "_probe_html", "_work", ".ul_profile"}


def clean_junk() -> None:
    """渲染/自测会在包里留下缓存，打包前清掉。"""
    for base, dirs, _files in os.walk(PKG, topdown=True):
        for d in list(dirs):
            if d in JUNK_DIRS:
                shutil.rmtree(os.path.join(base, d), ignore_errors=True)
                dirs.remove(d)


def make_zip() -> str:
    out = os.path.join(DIST, f"{PKG_NAME}_v{VERSION}.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(PKG):
            dirs[:] = [d for d in dirs if d not in JUNK_DIRS]
            for fn in files:
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, DIST))
    return out


def write_manifest() -> None:
    """生成 文件清单.md：每个文件一行备注，并标出没写备注的（防漏）。"""
    import datetime

    rows_by_dir: dict[str, list[tuple[str, int]]] = {}
    for base, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d not in JUNK_DIRS]
        rel_dir = os.path.relpath(base, PKG)
        for fn in sorted(files):
            if fn == "文件清单.md":
                continue
            size = os.path.getsize(os.path.join(base, fn))
            rows_by_dir.setdefault(rel_dir, []).append((fn, size))

    out = [
        f"# 文件清单 — {PKG_NAME} v{VERSION}",
        "",
        f"生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M}",
        "",
        "★ = 你会经常打开或需要修改的文件。",
        "",
        "先看 `使用说明.md`（或双击 `0_看介绍文档.bat` 看网页版）。",
        "要跑起来就按 bat 前面的数字顺序点。",
        "",
    ]

    missing = []
    for rel_dir in sorted(rows_by_dir, key=lambda d: (d != ".", d)):
        title = "包根目录" if rel_dir == "." else rel_dir
        note = DIR_NOTES.get(rel_dir, "")
        out.append(f"## {title}" + (f" — {note}" if note else ""))
        out.append("")
        out.append("| 文件 | 大小 | 说明 |")
        out.append("|:---|---:|:---|")
        for fn, size in rows_by_dir[rel_dir]:
            # 先按完整相对路径找（同名文件如 __init__.py 需要区分），再退回文件名
            key = fn if rel_dir == "." else os.path.join(rel_dir, fn)
            n = FILE_NOTES.get(key) or FILE_NOTES.get(fn, "")
            if not n:
                missing.append(os.path.join(rel_dir, fn))
                n = "（未标注）"
            out.append(f"| `{fn}` | {size / 1024:.1f} KB | {n} |")
        out.append("")

    out += [
        "## 当前状态一句话",
        "",
        "- **TÜV Rheinland**：能查，已验证可用（直连即可）。",
        "- **VDE**：接口和解析都写好并通过测试；**开 VPN/加速器后已实测跑通**，"
        "工具自动走系统代理。直连时被公司网关拦。",
        "- **UL Prospector**：**已打通（E502081 实测成功）**——装 Playwright + 首次登录后，"
        "用 suggest→qm 精确查询。有 Cloudflare，靠真浏览器(Edge)过。",
        "- **IECEE**：开代理后应可通（CB 证书最权威的源）。",
        "- 详情见 `使用说明.md`，实测数据见 `网络现状快照.txt` 和 `网络诊断快照.txt`。",
        "",
        "## 还差什么才能正式用",
        "",
        "1. 把报备清单 xlsx 放进来，跑 `3_查看表格列结构.bat`，把列字母填进 "
        "`cert_verify\\config.py`。",
        "2. 要查 VDE/IECEE 就先开 VPN/加速器（工具自动走系统代理）；"
        "或让 IT 把 `vde.com`、`certificates.iecee.org` 等加白名单。",
        "",
    ]

    with open(os.path.join(PKG, "文件清单.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    if missing:
        print(f"[warn] 以下文件没写备注，请补 FILE_NOTES: {missing}")


def render_doc_into_pkg() -> None:
    """在包目录里直接渲染一份 HTML，交付方无需装 R/pandoc 就能看。"""
    import subprocess
    r = subprocess.run([sys.executable, "-m", "cert_verify.render_doc"],
                       cwd=PKG, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    html = os.path.join(PKG, "介绍文档.html")
    if os.path.exists(html):
        print(f"介绍文档已渲染: {html}")
    else:
        print("介绍文档未能渲染成 HTML（Rmd 源文件已打包，可直接阅读）")
        if r.stdout:
            print("  " + r.stdout.strip()[:300])


def main() -> None:
    os.makedirs(DIST, exist_ok=True)
    copy_code()
    write_bats()
    write_readme()
    write_migration_guide()
    render_doc_into_pkg()
    clean_junk()
    write_manifest()          # 放最后，好把已生成的 HTML 也算进清单
    z = make_zip()

    lines = [f"打包目录: {PKG}",
             f"压缩包  : {z}  ({os.path.getsize(z) / 1024:.0f} KB)",
             "", "目录内容:"]
    n = 0
    for base, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs if d not in JUNK_DIRS]
        rel = os.path.relpath(base, PKG)
        for fn in sorted(files):
            n += 1
            lines.append("  " + (fn if rel == "." else os.path.join(rel, fn)))
    lines.insert(2, f"文件数  : {n}")

    text = "\n".join(lines)
    print(text)
    with open(os.path.join(SRC, "_build_log.txt"), "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
