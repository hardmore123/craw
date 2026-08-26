@echo off
REM ============================================================
REM 4_试跑10条.bat
REM 作用: 只核对前 10 条，验证列映射配对了没
REM 用法: 把 xlsx 文件拖到本 bat 的图标上；或双击后粘贴路径
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
set "XLSX=%~1"
if "%XLSX%"=="" set /p XLSX=把 xlsx 拖到本窗口后回车:
echo === 只跑前 10 条，先确认结果对不对 ===
%PYEXE% -m cert_verify.cli run "%XLSX%" --limit 10 --detail-col N
echo.
pause
