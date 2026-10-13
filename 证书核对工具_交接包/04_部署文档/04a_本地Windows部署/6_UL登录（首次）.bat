@echo off
REM ============================================================
REM 6_UL登录（首次）.bat
REM 作用: UL 首次登录（弹 Edge 人工登一次，存登录态）；需先装 Playwright
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
echo === UL Prospector 首次登录（需已装 Playwright）===
echo 会弹出 Edge 窗口，请在里面登录 UL，脚本自动检测并存登录态。
echo 没装 Playwright 就先跑： %PYEXE% -m pip install playwright
%PYEXE% -m cert_verify.ul_login
echo.
pause
