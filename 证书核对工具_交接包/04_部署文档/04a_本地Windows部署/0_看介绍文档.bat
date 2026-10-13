@echo off
REM ============================================================
REM 0_看介绍文档.bat
REM 作用: 打开介绍文档 HTML；没有就先渲染再打开
REM 用法: 直接双击
REM 顺序: 按文件名数字从小到大跑 —— 0_看介绍文档.bat → 1_安装依赖.bat → 2_网络自检.bat → 3_查看表格列结构.bat → 4_试跑10条.bat → 5_全量核对.bat → 6_UL登录（首次）.bat → 9_自测（验证程序本身）.bat → 一键启动.bat → 启动网页.bat
REM 说明文档: 使用说明.md / 文件清单.md
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0_py.bat"
if exist "介绍文档.html" (
    echo 打开已生成的 介绍文档.html
    start "" "介绍文档.html"
    goto :eof
)
echo === 渲染介绍文档（有 R 用 rmarkdown，否则用 pandoc）===
%PYEXE% -m cert_verify.render_doc
if exist "介绍文档.html" (
    start "" "介绍文档.html"
) else (
    echo.
    echo 没能生成 HTML。直接用记事本或 VS Code 打开 cert_verify\介绍文档.Rmd 也能看。
    pause
)
