@echo off
REM ============================================================
REM 一键启动.bat
REM 作用: ★★ 新电脑首次用：自动查Python→装依赖→启动网页（推荐）
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
echo ============================================================
echo   证书核对工具 · 一键启动（新电脑首次用这个）
echo ============================================================
echo.
echo [1/3] 检查 Python ...
%PYEXE% --version >nul 2>&1
if errorlevel 1 (
    echo   [x] 未检测到 Python。请先安装 Python 3.10+，安装时务必勾选
    echo       "Add python.exe to PATH"，装完重开本窗口再双击本脚本。
    echo       下载地址： https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
%PYEXE% --version
echo.
echo [2/3] 安装依赖（首次需要联网，之后会跳过）...
%PYEXE% -m pip install -r cert_verify\requirements.txt
echo   - 安装 Playwright（查 UL 需要；只查 VDE/TUV 可忽略其报错）...
%PYEXE% -m pip install playwright
echo.
echo [3/3] 启动网页服务 ...
echo   浏览器会自动打开 http://127.0.0.1:8756
echo   ★★ 使用期间请【不要关闭】本窗口！用完再关。
echo   （查 VDE/UL 需先开 VPN/加速器；查 UL 首次在网页点「UL 登录」登一次）
echo ============================================================
echo.
%PYEXE% -m cert_verify.web
echo.
echo 服务已结束。按任意键关闭本窗口。
pause >nul
