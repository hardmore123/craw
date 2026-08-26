@echo off
REM ============================================================
REM 9_自测（验证程序本身）.bat
REM 作用: 造样例表→真实抓 TÜV→比对→写回，外加 VDE 解析器 20 项断言
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
echo === 造样例表 + 真实抓取 TUV + 比对 + 写回，验证程序可用 ===
%PYEXE% -m cert_verify.selftest
echo.
echo === VDE 解析器单测（不联网）===
%PYEXE% cert_verify\tests\test_vde_parse.py
echo.
pause
