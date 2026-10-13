@echo off
REM ============================================================
REM 1_安装依赖.bat
REM 作用: 装 openpyxl（只需跑一次）
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
echo === 安装依赖（只需跑一次）===
%PYEXE% -m pip install -r cert_verify\requirements.txt
echo.
echo 如果后面要抓 VDE / UL（需要浏览器渲染），再执行：
echo     %PYEXE% -m pip install playwright
echo     %PYEXE% -m playwright install chromium
echo.
pause
