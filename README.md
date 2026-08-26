# 证书核对工具

把「安全件报备清单」里的证书号，逐条到 **VDE / UL / TÜV Rheinland** 官网自动查询，比对制造商、型号、标准、证书状态等字段，输出沿用原表布局的两份 Excel（对比表 + 抓取表）。提供本地网页版和命令行版两种用法，原文件不改动，结果另存副本，不一致处标红。

## 目录结构

```text
.
├─ cert_verify/          # 程序主体：网页、CLI、VDE/UL/TÜV Provider、解密、测试
├─ Linux/                # 服务器部署：systemd 服务、UL 验证脚本、部署说明
├─ 本地Windows/          # Windows 交付包、输入数据、历史版本
├─ 项目文档与日志/       # 交接文档、周报/半月报、部署与测试记录
│  ├─ 本地版本/          # Windows 开发、打包、离线测试
│  └─ 服务器版本/        # Linux 部署与验证
└─ 项目整理总说明.md     # 项目总览与整理说明
```

## 快速开始（Windows 本地）

本机使用 Python 3.12。

```powershell
# 启动网页版
py -3.12 -m cert_verify.web --port 8756

# 重新打包交付版
py -3.12 cert_verify\build_package.py
```

交付包解压即用：进入 `本地Windows/交付/证书核对工具/`，双击 `一键启动.bat`。

## 服务器部署（Linux）

服务器使用 Python 3.11，实际项目目录 `/home/hisense/pro/Gitlab/DataGather/`。

```bash
python -m cert_verify.web --host 0.0.0.0 --port 8756 --no-browser
```

systemd 服务、切换步骤和排查经验见 `Linux/README.md`、`Linux/部署说明.md` 和 `项目文档与日志/服务器版本/`。

## 文档索引

- 项目总览：`项目整理总说明.md`
- 本地开发接手：`项目文档与日志/本地版本/HANDOFF_交接文档.md`
- 服务器最新状态：`项目文档与日志/服务器版本/2026-08-26-部署与验证摘要.md`
- 版本变化：`本地Windows/交付/历史版本/CHANGELOG.md`

## 安全说明

以下内容不进入 Git 或交付包（已在 `.gitignore` 控制）：UL 登录态 `cert_verify/.ul_profile/`、查询缓存、网页上传临时文件、业务 Excel、`.env`。
