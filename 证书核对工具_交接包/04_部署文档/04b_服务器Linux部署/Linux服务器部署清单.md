# 证书核对工具 · Linux 服务器部署清单

> 当前生产方案（做法 A，与 CertAgent 共用 80 端口）：浏览器访问 `http://10.18.87.51/craw/`；Nginx 在既有 80 站点中用 `location /craw/` 反代到本机 Python `127.0.0.1:8758`。前端 API 使用当前页面下的相对路径，因此浏览器请求为 `/craw/api/...`，由 `/craw/` 一并转发；`/api/` 入口同时保留给直接根路径兼容。
>
> 服务器：`10.18.87.51`；用户：`hisense`；项目：`/home/hisense/pro/Gitlab/DataGather`。

## 0. 端口和路径先记清楚

| 层 | 地址 | 用途 |
|---|---|---|
| 浏览器正式入口 | `http://10.18.87.51/craw/` | 其他电脑访问 |
| Nginx | `0.0.0.0:80` | 与 CertAgent、工时速写共用入口 |
| Python 后端 | `127.0.0.1:8758` | Nginx 上游，只允许本机访问 |
| 离线浏览器 | `.browser/chrome-linux64/chrome` | UL 查询用，绕过 Playwright CDN |
| 解密服务 | `10.18.87.51:8500` | Python 上传加密 xlsx 时调用 |

说明：前端 `app.js` 使用当前页面下的相对 API 路径；在 `/craw/` 页面中请求 `/craw/api/...`，由 `location /craw/` 转发到 Python 的 `/api/...`。保留 `location /api/` 以兼容直接根路径访问。若 80 站点的 `/craw/` 或 `/api/` 已被别的项目占用，才改用做法 B（独立 8757 端口，见附录）。

## 1. 更新代码并确认路径

```bash
cd /home/hisense/pro/Gitlab/DataGather
git pull
pwd
# 期望：/home/hisense/pro/Gitlab/DataGather
ls cert_verify/web/__main__.py Linux/deploy/certverify.service Linux/deploy/nginx-certverify.conf
# 三个文件都应存在
```

## 2. 确认 Python 环境

```bash
cd /home/hisense/pro/Gitlab/DataGather
python3 --version
# 期望：Python 3.11.x
python3 -m venv venv                         # 已存在时不会破坏现有环境
venv/bin/pip install -r cert_verify/requirements.txt
venv/bin/python -c "import openpyxl; print('openpyxl', openpyxl.__version__)"
venv/bin/python -c "import cert_verify.web.server; print('web import ok')"
```

## 3. 停止旧网页进程并确认端口

只停止证书网页，不要停止 `decryptor.service`：

```bash
ps -ef | grep '[c]ert_verify.web'
# 对输出中的旧网页 PID 执行：
kill <旧网页进程PID>

# 8756 是旧端口；生产 Nginx 使用既有 80 端口；8758 是 Python 内部端口
ss -ltnp | grep -E ':8756|:8758' || echo '8756/8758 均可按生产方案使用'
```

若 PID 属于 systemd，不要手工 kill，改用：

```bash
sudo systemctl stop certverify
```

## 4. 确认解密服务（不要在本次操作中重启）

```bash
sudo systemctl status decryptor.service --no-pager
curl -s -o /dev/null -w '%{http_code}\n' http://10.18.87.51:8500/
# 期望：active (running)，HTTP 200
```

## 5. 安装并验证 systemd

仓库服务文件已经固定：Python `127.0.0.1:8758`、Nginx 使用既有 `80` 端口、`--strict-port`、`HOME`、`LANG`、离线浏览器和解密环境变量。

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
# 期望：ExecStart 包含 --port 8758 --strict-port；Environment 包含 HOME、DECRYPT_URL、CERT_BROWSER_EXECUTABLE
sudo journalctl -u certverify -n 50 --no-pager
```

如果 8758 被占用，服务会明确失败并在日志中提示端口占用。先查出占用者并停止它，再 `sudo systemctl restart certverify`。

## 5.1 UL 离线浏览器（服务器无法访问 Playwright CDN 时）

服务器直连 `cdn.playwright.dev` 会超时，所以用仓库自带的离线 Chrome，不要执行 `playwright install chromium`。

```bash
cd /home/hisense/pro/Gitlab/DataGather
venv/bin/pip install playwright        # 只装 Python 包，不下载浏览器
mkdir -p .browser
venv/bin/python -c "import zipfile; zipfile.ZipFile('Linux/浏览器安装包/chrome-linux64.zip').extractall('.browser')"
# chrome 和 crashpad handler 都要可执行，否则报 Permission denied
chmod 755 .browser/chrome-linux64/chrome .browser/chrome-linux64/chrome_crashpad_handler
```

验证离线浏览器可启动：

```bash
venv/bin/python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(executable_path='/home/hisense/pro/Gitlab/DataGather/.browser/chrome-linux64/chrome', headless=True); print('offline browser ok', b.version); b.close(); p.stop()"
# 期望：offline browser ok ...
# 若报缺库：sudo /home/hisense/pro/Gitlab/DataGather/venv/bin/python -m playwright install-deps chromium
```

`certverify.service` 已固定 `CERT_BROWSER_EXECUTABLE` 指向该离线 Chrome，重启服务即可生效。

## 6. 安装并验证 Nginx（做法 A：共用 80 端口）

先确认正在生效的 80 站点文件；本服务器实际使用 `/etc/nginx/sites-available/agents`，并将同样内容同步到实体生效文件 `/etc/nginx/sites-enabled/agents`（不要只改 `default`）。确认 `/craw/` 和 `/api/` 未被其他项目占用：

```bash
grep -rn "location /craw\|location /api" /etc/nginx/sites-available/agents /etc/nginx/sites-enabled/agents
```

然后在 `server` 块中加入 `/craw/` 与 `/api/`：

```nginx
location = /craw { return 301 /craw/; }
location /craw/ {
    rewrite ^/craw/(.*)$ /$1 break;
    proxy_pass http://127.0.0.1:8758;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;
    client_max_body_size 50m;
}
location /api/ {
    proxy_pass http://127.0.0.1:8758;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;
    client_max_body_size 50m;
}
```

做法 A 下不要再启用独立的 8757 站点，避免两套配置冲突：

```bash
sudo rm -f /etc/nginx/sites-enabled/certverify
sudo nginx -t
# 期望：syntax is ok / test is successful
sudo systemctl reload nginx
```

本机验证：

```bash
curl -s -o /dev/null -w 'page=%{http_code}\n' http://127.0.0.1/craw/
curl -s http://127.0.0.1/craw/api/ul-status
# 期望：API 返回 JSON；直接根路径兼容入口为 /api/ul-status
```

若 page=502：看 `ss -ltnp | grep ':8758'` 和 `journalctl -u certverify`。
若 page=404：确认 `/craw/` 已写进正在生效的 80 站点文件。

## 7. ProductAgentPlatform 使用数据上报（可选，启用前需管理员提供有效 Key）

业务规则已固定：`agent_id=cert_verify`、固定 `user_id=certverify`；每次任务开始发送一次 `register`，只有任务成功完成时发送一次 `usage`；`benefit_minutes = 完成核对行数 × 3`。取消、被拦截、失败或完成 0 行不发送 usage。上报失败只记录 warning，不影响核对和结果文件生成。

先由平台管理员确认/注册 `cert_verify` agent 并取得专用 API Key。不要使用聊天记录中的疑似管理员 Key，也不要把 Key 写入代码、service 文件、Git 或文档。

在服务器项目根目录创建或编辑 `.env`：

```ini
PAP_ENABLED=true
PAP_COLLECTOR_URL=http://10.18.87.51:8001
PAP_API_KEY=在服务器上填写平台管理员提供的有效Key
PAP_AGENT_ID=cert_verify
PAP_USER_ID=certverify
PAP_MINUTES_PER_ROW=3
PAP_TIMEOUT=5
```

重启并检查配置已由 systemd 读取（输出中不要复制或公开 API Key）：

```bash
cd /home/hisense/pro/Gitlab/DataGather
sudo systemctl daemon-reload
sudo systemctl restart certverify
sudo systemctl status certverify --no-pager
curl -s http://10.18.87.51:8001/api/v1/health
```

完成一次真实成功核对后，在 Collector/Dashboard 检查 `register(user_id=certverify)` 和 `usage`，其中 `benefit_minutes` 应等于实际完成行数乘 3。不要用失败或取消任务验证 usage。

## 8. 其他电脑访问

80 端口通常已放行，无需额外 ufw 规则。其他电脑访问：

```text
http://10.18.87.51/craw/
```

不要访问 `:8758`（Python 后端只绑定 127.0.0.1）。

## 附录 · 做法 B（独立 8757 端口，`/api/` 冲突时使用）

```bash
sudo cp Linux/deploy/nginx-certverify.conf /etc/nginx/sites-available/certverify
sudo ln -sf /etc/nginx/sites-available/certverify /etc/nginx/sites-enabled/certverify
sudo nginx -t && sudo systemctl reload nginx
sudo ufw allow 8757/tcp
```

访问地址：`http://10.18.87.51:8757/craw/`。做法 A 与做法 B 只能启用一个。

## 9. “开始核对没有进度”的验证顺序

先用普通 `.xlsx`、勾选一张表、限制 1 条，确认链路，再跑全部：

1. 浏览器按 `F12` 打开 Network。
2. 点击“开始核对”，确认 `POST /craw/api/run` 返回 HTTP 200 且 JSON 有 `task_id`。
3. 随后每秒应有 `GET /craw/api/progress?id=...` 返回 HTTP 200。
4. 页面先显示“正在扫描 Excel，统计待核对行数…”，然后显示“正在查询官网…”。首次 UL/VDE 网络请求可能较慢，不再被误认为卡死。
5. 若 `/craw/api/run` 是 404/502，先修 Nginx/端口，不要反复点击按钮。
6. 若 progress 是 200 但长时间 0/N，检查证书号列是否选对、是否配置代理，以及 `sudo journalctl -u certverify -f`。

## 10. 重要运行约束

- `certverify` 只能运行一个实例；任务状态在内存中，不能用多 worker 或第二个网页进程。
- `.ul_profile/`、`.env`、业务 Excel 和 `cert_verify/web/_work/` 不提交 Git。
- Nginx 只反代，不读取 `/home/hisense/...` 下的静态文件，不需要 `chmod 777` 或 `setfacl`。

## 11. 断电重启保护（十一假期 / 机房断电场景）

> 背景：断电重启后 `certverify` 可能**自动恢复失败**，原因是 systemd 默认
> `StartLimitBurst=5` + `StartLimitIntervalSec=10s`：网络/解密服务/代理未就绪时主进程若
> 连续失败 5 次，systemd 会判定「放弃恢复」，服务永久停在 `failed`，直到有人手工 `start`。
> 另外 `After=network-online.target` 只保证网卡 up，**不等**解密服务 `:8500`、代理、路由真正就绪。

`Linux/deploy/certverify.service` + 新增的 `Linux/deploy/cert_verify_ready.sh` 已内置保护：

| 机制 | 配置 | 作用 |
|---|---|---|
| 启动前依赖探测 | `ExecStartPre=/.../cert_verify_ready.sh` | 依次等路由(≤60s) → 解密服务8500(≤60s) → `CERT_PROXY`代理(≤60s)，**任一超时只告警、不阻塞启动** |
| 崩溃自动恢复 | `Restart=on-failure` + `RestartSec=5` | 主进程异常退出 5 秒后重试 |
| 无限次启动尝试 | `StartLimitIntervalSec=0` | 关掉「10 秒内失败 5 次即判死」的限制，下次尝试就能拉起 |
| 弱依赖 decryptor | `After=`/`Wants=decryptor.service`（非 `Requires`） | 解密服务没起来只是「加密上传报错」，网页本体照常服务，不会连锁 failed |
| 探测时间放宽 | `TimeoutStartSec=240` | ExecStartPre 最坏约 182s，默认 90s 会误杀 |

### 11.1 更新到服务器

```bash
cd /home/hisense/pro/Gitlab/DataGather
git pull   # 或 scp 覆盖 Linux/deploy/ 下的 certverify.service + cert_verify_ready.sh

chmod +x Linux/deploy/cert_verify_ready.sh        # 确保可执行
sudo cp -f Linux/deploy/certverify.service /etc/systemd/system/certverify.service
sudo systemctl daemon-reload
sudo systemctl restart certverify
sudo systemctl is-active certverify               # 期望 active
```

### 11.2 验证（模拟断电与依赖缺失）

```bash
# 1) 脚本语法自检
bash -n /home/hisense/pro/Gitlab/DataGather/Linux/deploy/cert_verify_ready.sh && echo OK

# 2) 手动跑一次探测脚本，看 /tmp/cert_verify_ready.log 三段依赖分别就绪/告警
bash /home/hisense/pro/Gitlab/DataGather/Linux/deploy/cert_verify_ready.sh
cat /tmp/cert_verify_ready.log

# 3) 验证「解密服务挂了不影响网页服务」（弱依赖）：
sudo systemctl stop decryptor.service   # 解密服务不存在则跳过
sudo systemctl restart certverify
sudo systemctl is-active certverify     # 期望 active —— 降级可用
sudo systemctl start decryptor.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8758/
```

### 11.3 断电后的验证

开机后执行：

```bash
systemctl status certverify            # 应在运行，无需人工干预
journalctl -u certverify -b            # 本次开机日志，无反复 Failed/StartLimit 报错
systemctl show certverify -p NRestarts # 已自动重启次数
```

注意：

- 服务器上如果还没有安装 `decryptor.service`（unit 不在本仓库），`Wants=`/`After=` 引用不存在的 unit 会被 systemd 自动忽略，不影响启动。
- 不要把 `StartLimitIntervalSec=0` 改回默认，否则断电场景会再次永久 `failed`。
