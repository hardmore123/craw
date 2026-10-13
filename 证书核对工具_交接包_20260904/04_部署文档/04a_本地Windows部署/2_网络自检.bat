@echo off
REM ============================================================
REM 2_网络自检.bat
REM 作用: ① 各机构网站可达性 ② 分层诊断（浏览器能上、脚本不能时看这个）
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
echo === 检查各认证机构网站能不能连上（抓取前必跑）===
%PYEXE% -m cert_verify.cli netcheck
echo.
echo === 分层诊断：如果浏览器能打开但脚本不行，看这里 ===
%PYEXE% -m cert_verify.cli netdiag
echo.
pause
