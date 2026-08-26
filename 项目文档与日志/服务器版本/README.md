# 服务器版本文档（Linux）

面向在 Linux 服务器上部署网页服务、解密服务和 UL 查询的人。服务器使用 Python 3.11。

## 文件清单

| 文件 | 用途 |
|---|---|
| `2026-08-26-部署与验证摘要.md` | 最新服务器状态：解密服务、网页服务、Linux UL、VDE/TÜV、v2.7 打包、systemd 切换待办 |
| `参考部署经验CertAgent/部署与使用指南.md` | CertAgent 项目部署与使用指南（参考） |
| `参考部署经验CertAgent/部署问题排查日志.md` | CertAgent 部署 21 个问题的排查记录（参考） |

## 说明

- `参考部署经验CertAgent/` 来自另一个项目 CertAgent，**不是本工具的运行代码**。其中关于 systemd、Nginx 权限（ACL）、环境变量加载、透明加密解密服务的经验被本项目借鉴，保留作参考。
- 本工具服务器实际项目目录：`/home/hisense/pro/Gitlab/DataGather/`
- 解密服务固定地址：`http://10.18.87.51:8500/parse_file`

## 相关位置

- Linux 部署文件与步骤：`Linux/README.md`、`Linux/部署说明.md`、`Linux/deploy/certverify.service`
- 本地开发文档见同级 `../本地版本/`
