# Linux 服务器文件区

## 用途

这里集中保存证书核对工具在 Linux 服务器上的部署文件、UL Linux 验证脚本、浏览器安装包和部署说明。

## 目录

- `服务器部署清单.md`：★ 可直接照做的分步部署清单，每步带验证命令（重点规避 Nginx 权限与端口/路径坑）。
- `deploy/certverify.service`：systemd 网页服务定义（含 HOME/LANG/解密环境变量）。
- `deploy/nginx-certverify.conf`：Nginx 纯反向代理配置（不读文件，避开 ACL 坑）。
- `deploy/test_ul_linux.py`：Linux Chromium/UL 验证脚本。
- `浏览器安装包/`：Chromium 压缩包，仅在服务器缺少浏览器时使用。
- `部署说明.md`：完整的 Linux 架构、网络、UL、解密和 systemd 说明（背景分析）。

## 部署从哪开始

**直接照做请看 `服务器部署清单.md`**。`部署说明.md` 是背景与可行性分析，适合先理解原理。

## 服务器实际目录

```text
/home/hisense/pro/Gitlab/DataGather/
```

将仓库或 v2.7 交付包中的运行内容放到该目录根部，目录中应直接存在：

```text
cert_verify/
venv/
deploy/            # 可从本目录复制
```

不要把 zip 解压成多套 `证书核对工具/证书核对工具/` 嵌套目录。

## systemd 切换

先确认并停止旧的 nohup 网页进程，但不要停止 `decryptor.service`：

```bash
ps -ef | grep '[c]ert_verify.web'
kill <旧网页进程PID>

cd /home/hisense/pro/Gitlab/DataGather
sudo cp deploy/certverify.service /etc/systemd/system/certverify.service
sudo systemctl daemon-reload
sudo systemctl enable --now certverify
sudo systemctl status certverify --no-pager
curl -I http://127.0.0.1:8756/
sudo journalctl -u certverify -n 50 --no-pager
```

## 已验证的服务

- `decryptor.service`：`active (running)`，监听 `0.0.0.0:8500`。
- 真实透明加密 `.xlsx` 已通过解密服务还原并成功交给 openpyxl 解析。
- Linux Chromium UL 独立登录态已生成，`E502081` 查询返回 `found`。
- 网页服务固定使用项目 venv，默认不开浏览器窗口。

## 安全要求

- 不要把 Linux `.ul_profile/`、`storage_state.json`、`.env` 或业务 Excel 复制回 Git 或 Windows 交付包。
- UL 首次验证可临时使用 Xvfb/VNC；日常查询不需要一直运行 VNC。
- 查询间隔建议 8~15 秒固定间隔并加入随机抖动，遇到 Cloudflare 应人工处理，不得无限刷新。
