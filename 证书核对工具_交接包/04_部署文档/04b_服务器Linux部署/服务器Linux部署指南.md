# 服务器 Linux 部署指南

> 本文件专门讲在 **Linux 服务器** 如何部署证书核对工具。本地 Windows 部署见同级的 `04a_本地Windows部署/`。
>
> 服务器：`10.18.87.51`；用户：`hisense`；项目目录：`/home/hisense/pro/Gitlab/DataGather`

---

## 一、生产架构（做法 A，已部署）

```
其他电脑浏览器
    │  http://10.18.87.51/craw/
    ▼
Nginx (0.0.0.0:80)           ← 与 CertAgent、工时速写共用入口
    │  location /craw/ → 反代到 127.0.0.1:8758
    │  location /api/  → 反代到 127.0.0.1:8758
    ▼
Python 后端 (127.0.0.1:8758) ← 仅本机，systemd 托管
    │  cert_verify/web/server.py
    ├── 透明加密检测 + 解密服务 (10.18.87.51:8500)
    ├── UL 离线浏览器 (.browser/chrome-linux64/chrome)
    └── VDE/TÜV 走系统代理（境外站需代理）
```

| 层 | 地址 | 用途 |
|---|---|---|
| 浏览器正式入口 | `http://10.18.87.51/craw/` | 其他电脑访问 |
| Nginx | `0.0.0.0:80` | 与 CertAgent、工时速写共用入口 |
| Python 后端 | `127.0.0.1:8758` | Nginx 上游，只允许本机访问 |
| 离线浏览器 | `.browser/chrome-linux64/chrome` | UL 查询用，绕过 Playwright CDN |
| 解密服务 | `10.18.87.51:8500` | Python 上传加密 xlsx 时调用 |

---

## 二、逐条部署清单

### 1. 更新代码并确认路径

```bash
cd /home/hisense/pro/Gitlab/DataGather
git pull
pwd
# 期望：/home/hisense/pro/Gitlab/DataGather
ls cert_verify/web/__main__.py Linux/deploy/certverify.service
# 两个文件都应存在
```

### 2. 确认 Python 环境

```bash
cd /home/hisense/pro/Gitlab/DataGather
python3 --version
# 期望：Python 3.11.x
python3 -m venv venv                         # 已存在时不会破坏现有环境
venv/bin/pip install -r cert_verify/requirements.txt
venv/bin/pip install playwright              # UL 需要
venv/bin/python -c "import openpyxl; print('openpyxl', openpyxl.__version__)"
venv/bin/python -c "import cert_verify.web.server; print('web import ok')"
```

### 3. 停止旧网页进程（不要停解密服务）

```bash
# 只停证书网页，不要停 decryptor.service
ps -ef | grep '[c]ert_verify.web'
kill <旧网页进程PID>           # 对上面输出的旧 nohup PID 执行

# 若 PID 属于 systemd，不要手工 kill：
sudo systemctl stop certverify

# 确认端口
ss -ltnp | grep -E ':8756|:8758' || echo '端口可用'
```

### 4. 确认解密服务（不要在本次操作中重启）

```bash
sudo systemctl status decryptor.service --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://10.18.87.51:8500/
# 期望：active (running)，HTTP 200
```

### 5. UL 离线浏览器（服务器无法访问 Playwright CDN 时）

```bash
cd /home/hisense/pro/Gitlab/DataGather
mkdir -p .browser
venv/bin/python -c "import zipfile; zipfile.ZipFile('Linux/浏览器安装包/chrome-linux64.zip').extractall('.browser')"
# chrome 和 crashpad handler 都要可执行
chmod 755 .browser/chrome-linux64/chrome .browser/chrome-linux64/chrome_crashpad_handler

# 验证离线浏览器可启动
venv/bin/python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(executable_path='/home/hisense/pro/Gitlab/DataGather/.browser/chrome-linux64/chrome', headless=True); print('browser ok'); b.close(); p.stop()"
```

### 6. 安装并启用 systemd

```bash
cd /home/hisense/pro/Gitlab/DataGather
sudo cp Linux/deploy/certverify.service /etc/systemd/system/certverify.service
sudo systemctl daemon-reload
sudo systemctl enable certverify
sudo systemctl restart certverify
sudo systemctl status certverify --no-pager
```

逐条验证：
```bash
ss -ltnp | grep ':8758'
# 期望：python 监听 127.0.0.1:8758
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8758/
# 期望：200
systemctl show certverify -p ExecStart -p Environment
# 期望：ExecStart 含 --port 8758 --strict-port；Environment 含 HOME、DECRYPT_URL、CERT_BROWSER_EXECUTABLE
sudo journalctl -u certverify -n 50 --no-pager
```

### 7. Nginx 配置（做法 A，共用 80 端口）

在既有 80 站点中加：
```nginx
location /craw/ {
    proxy_pass http://127.0.0.1:8758/;
    client_max_body_size 50m;       # 不小于程序 MAX_UPLOAD(30MB)
}
location /api/ {
    proxy_pass http://127.0.0.1:8758/api/;
    client_max_body_size 50m;
}
```

验证：其他电脑访问 `http://10.18.87.51/craw/` 能打开网页。

### 8. UL 服务器登录（首次）

```bash
# 需要有显示环境（Xvfb/VNC）
xvfb-run -a venv/bin/python -m cert_verify.ul_login
# 在浏览器里人工登录 UL Prospector，登录态存到 cert_verify/.ul_profile/
# 之后可以无头模式运行

# 验证 UL 查询
venv/bin/python -m cert_verify.cli query E502081 --agency UL
# 期望：status=found
```

---

## 三、systemd 服务文件说明（certverify.service）

```ini
[Unit]
Description=Cert Verify Web
After=network-online.target
Wants=network-online.target
After=decryptor.service
Wants=decryptor.service
# 断电重启保护：关闭"10s内失败5次即永久failed"限制
StartLimitIntervalSec=0
StartLimitBurst=0

[Service]
Type=simple
User=hisense
WorkingDirectory=/home/hisense/pro/Gitlab/DataGather
Environment="HOME=/home/hisense"
Environment="CERT_BROWSER_EXECUTABLE=/home/hisense/pro/Gitlab/DataGather/.browser/chrome-linux64/chrome"
Environment="LANG=C.UTF-8"
Environment="LC_ALL=C.UTF-8"
EnvironmentFile=-/home/hisense/pro/Gitlab/DataGather/.env
Environment="DECRYPT_URL=http://10.18.87.51:8500/parse_file"
Environment="DECRYPT_ENABLED=true"
Environment="DECRYPT_TIMEOUT=60"
ExecStart=/home/hisense/pro/Gitlab/DataGather/venv/bin/python -m cert_verify.web --host 127.0.0.1 --port 8758 --strict-port --no-browser
ExecStartPre=/home/hisense/pro/Gitlab/DataGather/Linux/deploy/cert_verify_ready.sh
Restart=on-failure
RestartSec=5
TimeoutStartSec=240

[Install]
WantedBy=multi-user.target
```

关键设计点：
- **弱依赖 decryptor**（`Wants=` 不是 `Requires=`）：解密服务挂了网页本体仍可服务
- **ExecStartPre 探测依赖**：路由/解密服务/代理就绪探测，超时不阻塞启动
- **StartLimitIntervalSec=0**：断电场景不会因反复失败被永久拉黑
- **--strict-port**：8758 被占用时直接失败（不会静默换端口）
- **单进程**：任务状态在内存，不能同时启动第二个网页实例

---

## 四、断电重启验证

开机后执行：
```bash
systemctl status certverify            # 应在运行，无需人工干预
journalctl -u certverify -b            # 本次开机日志，无反复 Failed/StartLimit 报错
systemctl show certverify -p NRestarts # 已自动重启次数
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8758/   # 期望 200
```

---

## 五、服务器独有的坑（CertAgent 没有）

### ★ 难点 1：UL 的 Playwright + Cloudflare（头号风险）

| 障碍 | 说明 | 对策 |
|---|---|---|
| Playwright CDN 不可达 | 服务器无法下载 chromium | 用离线 Chrome（`.browser/chrome-linux64/chrome`） |
| Edge 不在 Linux | `channel=msedge` 只能 Windows | Linux 用 chromium（离线包） |
| Cloudflare 挑战 | 可能要求人工验证 | 首次用 Xvfb 有头模式登录，之后切无头 |
| 登录态跨平台 | Windows 的 `.ul_profile` 不能复制到 Linux | Linux 上各登一次 |

UL 上 Linux 的分步验证清单（真要做 UL 时按此走）：
1. `python -m playwright install --with-deps chromium` 装浏览器+依赖（或用离线包）
2. `xvfb-run -a python -c "from playwright.sync_api import ..."` 测能否起 Chromium
3. xvfb 有头模式打开 UL 首页，人工过一次 Cloudflare + 登录
4. 复用 profile 跑一条查询，看能否直接进结果页
5. 切 headless=True 再跑一条，观察是否仍被 Cloudflare 拦
6. 把查询间隔调大（如 8~15s + 抖动），观察长时间跑是否被风控

### 难点 2：systemd 不加载 .env

`os.getenv()` 读的配置（如 CERT_PROXY），systemd 下不会自动加载 .env。
**必须写 `EnvironmentFile=` 或让程序自己 load dotenv**（已在 service 文件中配置）。

### 难点 3：中文文件名乱码

systemd 下 LANG 默认空，中文文件名/表头会乱码。
**已在 service 文件中设 `LANG=C.UTF-8` 和 `LC_ALL=C.UTF-8`**。

---

## 六、日常运维命令

```bash
# 查看状态
sudo systemctl status certverify --no-pager

# 查看日志（最近50行）
sudo journalctl -u certverify -n 50 --no-pager

# 查看本次开机日志
sudo journalctl -u certverify -b --no-pager

# 重启
sudo systemctl restart certverify

# 停止 / 启动
sudo systemctl stop certverify
sudo systemctl start certverify

# 解密服务
sudo systemctl status decryptor.service --no-pager
sudo systemctl restart decryptor.service

# 更新代码后
cd /home/hisense/pro/Gitlab/DataGather
git pull
sudo systemctl restart certverify

# 验证网页
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8758/
# 从其他电脑访问
curl -s -o /dev/null -w '%{http_code}\n' http://10.18.87.51/craw/
```

---

## 七、平台使用数据上报（暂不部署）

如需启用平台上报（`platform_report.py`），需：
1. 在 ProductAgentPlatform 注册 `cert_verify` agent
2. 取得专用 API Key
3. 在服务器 `.env` 中填 `PAP_ENABLED=true` 和 `PAP_API_KEY=<专用key>`
4. `sudo systemctl daemon-reload && sudo systemctl restart certverify`

> API Key 不写入代码、service 文件、Git 或聊天记录。
> 详见 `08b_服务器Linux配置/部署前信息清单_平台上报.md`。

---

## 八、回滚

如果新版本有问题需要回滚：
```bash
cd /home/hisense/pro/Gitlab/DataGather
git log --oneline -5          # 找到上一个稳定版本
git checkout <上一个稳定commit>
sudo systemctl restart certverify
```

详见 `08b_服务器Linux配置/回滚说明_平台上报.md`。
