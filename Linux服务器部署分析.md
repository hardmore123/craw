# 证书核对工具 · Linux 服务器部署分析

> 借鉴 CertAgent 项目《部署与使用指南》《部署问题排查日志》(21 个问题) 的经验，
> 结合本项目实际，评估部署到 Linux 服务器的可行性、风险与方案。

---

## 一、两个项目对比：哪些经验能直接借鉴

| 维度 | CertAgent（已上线 Linux） | 本项目（证书核对） | 借鉴度 |
|---|---|---|---|
| 后端框架 | FastAPI + Uvicorn（多进程） | Python 标准库 http.server（单进程） | 部分 |
| 前端 | React SPA，需 `npm run build` | 纯静态 HTML/JS/CSS，无需构建 | 简化 |
| 反向代理 | Nginx 必需 | 可选（自带静态服务） | 部分 |
| 文档解析 | **LibreOffice 转 .doc（最大坑）** | openpyxl 读 .xlsx（跨平台，无坑） | **不适用** |
| 浏览器自动化 | 无 | **Playwright+Edge 过 Cloudflare（本项目最大坑）** | 新问题 |
| 任务状态 | 内存（踩过多 worker 坑） | 内存（TaskManager） | **直接借鉴** |
| 外网访问 | 内网 LLM 网关 | 境外官网 VDE/UL/TÜV | 需评估 |
| 环境变量 | 踩过 systemd 不加载 .env 坑 | 有 CERT_PROXY 等 | **直接借鉴** |

**一句话**：CertAgent 花大力气解决的 LibreOffice 问题我们没有；但我们有它没有的
**无头浏览器过 Cloudflare** 问题，这是本项目上 Linux 的头号难点。

---

## 二、可直接借鉴的经验（拿来即用）

### 1. systemd 托管服务（★ 强烈借鉴）
把 `python -m cert_verify.web` 做成 systemd 服务，开机自启 + 崩溃重启。
教训借鉴：**必须显式设 HOME 和 EnvironmentFile**（CertAgent 问题 8、16 的坑）。

```ini
# /etc/systemd/system/certverify.service
[Unit]
Description=Cert Verify Web
After=network.target

[Service]
Type=simple
User=hisense
WorkingDirectory=/home/hisense/certverify
Environment="HOME=/home/hisense"
EnvironmentFile=/home/hisense/certverify/.env      # CERT_PROXY 等在此
ExecStart=/home/hisense/certverify/venv/bin/python -m cert_verify.web --host 0.0.0.0 --port 8756
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> 借鉴 CertAgent 问题 16 教训：`os.getenv()` 读的配置（如 CERT_PROXY），
> systemd 下不会自动加载 .env，**必须写 EnvironmentFile= 或让程序自己 load dotenv**。

### 2. 单进程 / 内存任务状态（★ 直接借鉴）
CertAgent 问题 5：多 worker 导致任务状态不共享、列表闪动 → 改单 worker。
本项目 `TaskManager` 也是内存态，**必须保持单进程**（stdlib ThreadingHTTPServer
天然单进程多线程，符合要求，别改成多进程/多实例）。

### 3. 端口一致性（借鉴问题 7）
CertAgent 因文档端口(8080)与实际(8123)不一致，同事访问失败。
本项目定端口后，`server.py`、systemd、Nginx、文档要全部同步（默认 8756）。

### 4. 绑定地址与防火墙
- 本项目当前 `serve()` 默认 `host=127.0.0.1`（仅本机）。服务器要给他人访问需
  **绑 0.0.0.0**（已支持 `--host` 参数）或用 Nginx 反代。
- 放行端口：`sudo ufw allow 8756/tcp`。

### 5. Nginx 权限/ACL（若用 Nginx，借鉴问题 1/10/20）
本项目 static 由自身服务托管，**可不用 Nginx**（直接暴露 8756）。
若为了统一入口/HTTPS 加 Nginx 反代，只做 `proxy_pass` 到 127.0.0.1:8756 即可，
**不让 Nginx 直接读文件**，就能绕开 CertAgent 那一串 setfacl/ACL 坑。
上传大小：Nginx 需 `client_max_body_size 30m;`（与程序 MAX_UPLOAD 一致）。

### 6. 版本快照习惯（借鉴问题 21）
已在做：`dist/历史版本/` 按版本归档 zip+日志+说明。继续保持。

---

## 三、本项目独有的难点（CertAgent 没有）

### ★ 难点 1：UL 的 Playwright + Edge + Cloudflare（头号风险）

本项目 UL 查询靠 Playwright 驱动**本机 Edge（channel=msedge）**、用持久化登录态
`.ul_profile` 过 Cloudflare。搬到 Linux 服务器有多重障碍：

| 障碍 | 说明 | 对策 |
|---|---|---|
| Linux 无 Edge msedge 通道 | `channel=msedge` 在 Linux 默认不存在 | 改用 `playwright install chromium`，代码里 channel 改 chromium |
| 缺系统依赖 | 无头浏览器需一堆 .so | `playwright install-deps` 或装 `libnss3 libatk...` |
| 无头被 Cloudflare 识别 | headless 更易触发人机验证 | 需 xvfb 提供虚拟显示 + 非无头，或复用已过验证的 profile |
| 登录态如何过去 | `.ul_profile` 是本机 Edge 的，Linux chromium 用不了 | 需在服务器上用 xvfb 跑一次有头登录，重新生成 profile |
| systemd 下跑浏览器 | 类比 CertAgent 的 LibreOffice-in-systemd 不可靠 | 高风险，建议先在服务器手动 xvfb-run 验证能否过 CF |

> **与 CertAgent 问题 11/14 同构**：LibreOffice/Word COM 在 systemd headless 下不可靠，
> 是"平台/环境边界"问题。UL 的无头浏览器过 Cloudflare 很可能同样难缠。

**务实建议（分级）**：
- **方案 A（推荐先做）**：服务器只跑 **VDE + TÜV**（纯 HTTP，零浏览器依赖，Linux 完美支持）。
  UL 继续在个人 Windows 电脑上跑（本地网页版）。—— 覆盖大部分需求、零风险。
- **方案 B（进阶）**：服务器装 chromium + xvfb，代码 channel 改 chromium，
  在服务器有头（xvfb）登录一次 UL 生成 profile，再评估无头能否稳定过 CF。
- **方案 C（最稳但重）**：UL 单独放一台 Windows 机器做"UL 查询微服务"，
  Linux 主服务通过 HTTP 调它（类比 CertAgent 问题 14 的 Windows Word COM 微服务思路）。

#### UL 上 Linux 的具体做法（方案 B 展开）

**1) 用 Playwright 自带 Chromium，别用 Edge 通道**
```bash
python -m playwright install chromium
# Ubuntu/Debian 一次装好系统依赖（推荐）
python -m playwright install --with-deps chromium
```
代码里把 UL 浏览器从 `channel="msedge"` 改成 Chromium：
```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)   # Linux 用自带 chromium
    page = browser.new_page()
    page.goto("https://iq.ulprospector.com/en")
    browser.close()
```
> 改造点在 `providers/ul.py` 与 `ul_login.py` 的 `_launch_persistent`：
> channel 做成可配置（Windows=msedge / Linux=chromium），按平台或环境变量自动选。

**2) 无头不友好时：headed Chromium + Xvfb（先测这个，别急着"绕过"）**

先区分两种情况，不要一看到 Cloudflare 就想绕过：
```
正常页面 → 正常登录 → 访问目标页        ← 我们要的
进入 Cloudflare Challenge（人机验证）    ← 要处理的
```
若真实浏览器能访问、而 headless 经常触发验证，用虚拟显示跑**有头** Chromium：
```bash
sudo apt install -y xvfb
xvfb-run -a python -m cert_verify.web        # 或在 systemd 里配虚拟显示
```
链路：`Linux 服务器 → Xvfb 虚拟显示器 → Chromium(headed) → Playwright → UL`。
相比纯 headless，有头模式下浏览器兼容/验证问题更少、更易排查。

**3) 登录态持久化做成独立模块（非常重要）**

Windows 的 `.ul_profile`（Edge）**搬不到 Linux**（浏览器不同）。要在服务器上重新生成，
并把"登录态"当作独立部署模块。用 Playwright 持久化上下文：
```python
with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="./data/ul-profile",   # Linux 上专属 profile 目录
        headless=False)                       # 首次配 xvfb 有头登录
    page = context.new_page()
    page.goto("https://iq.ulprospector.com/en")
    input("首次登录/过验证完成后按回车...")     # 人工完成一次
    context.close()
```
之后每次：读 `ul-profile` → 恢复 Cookie/LocalStorage 登录态 → 直接访问 UL。
比"每次自动填账号密码登录"稳定得多。

**4) 不要把账号密码写进代码**
```python
import os
username = os.getenv("UL_USERNAME")
password = os.getenv("UL_PASSWORD")
```
放环境变量 / `.env`（systemd `EnvironmentFile=`）/ 生产用 Secret Manager，仓库不提交。

**5) 首次需人工验证 → Human-in-the-loop（不要疯狂刷新）**
```
检测到 Cloudflare Challenge
  → 任务暂停、记录状态（如 status=need_ul_verify）
  → 人工完成验证
  → 保存 session（profile 落盘）
  → 恢复自动任务
```
把"需要人"的环节显式暴露成可恢复状态，而不是无限重试导致被风控升级。

**6) 若服务器 IP 长期被 Cloudflare 风控 → 从架构解决，不是"绕过"**

优先顺序：
1. **官方 API / 数据接口**：UL 若有 API / 数据下载 / 企业数据合作接口，优先用，最稳定。
2. **合法浏览器自动化**：Chromium + 持久化登录态 + **合理频率 + 退避重试**
   （对应本项目已做的"查询间隔 + 随机抖动"，服务器上应调大而非调到 1s）。
   绝不无限并发 / 无限刷新 / 疯狂轮询。
3. **Windows 浏览器 Worker（方案 C）**：若 UL 对 Linux 数据中心环境就是不稳定，
   把 UL 隔离到一台 Windows：
   ```
   Linux 主服务(调度/业务/Excel/API/日志) --HTTP/RPC--> Windows Worker(Playwright+Edge+UL)
   ```
   与"LibreOffice/Word COM 独立微服务"同一种工程思想：**把强平台依赖模块隔离出去**。

### 难点 2：外网可达性（服务器网络环境）
本机实测 VDE/TÜV/UL 无需 VPN 可直连，但**服务器所在网络不一定**：
- 若服务器在能直连境外的网络 → VDE/TÜV 直接可用。
- 若服务器在受限内网 → 需给服务器配代理（.env 里设 `CERT_PROXY=http://代理:端口`，
  程序 v2.6 起会自动探测代理存活）。
- 部署前先在服务器跑 `python -m cert_verify.cli netcheck` 确认三站可达性。

### ★ 难点 3：上传的 Excel 可能被加密（借鉴 CertAgent 问题 13/18）

本机上传的报备清单文件**可能带加密**，服务器上必须先解密才能读。分两种情况：

| 加密类型 | 表现 | 服务器影响 | 对策 |
|---|---|---|---|
| **透明加密（DLP）** | 本机打开正常，磁盘字节是密文 | 若用 scp/共享盘传到服务器 = 拿到密文，openpyxl 打不开 | 走公司解密服务还原（同 CertAgent `decrypt.py`）|
| **Excel 打开密码** | 文件是 OLE2 容器，非 zip | openpyxl 抛异常 | 提示用户去掉密码后另存 |

**关键判断（magic bytes）**：正常 .xlsx = ZIP，前 4 字节 `50 4B 03 04`（PK..）；
加密/OLE 容器 = `D0 CF 11 E0` 或随机字节 → openpyxl 无法解析。

**本项目 vs CertAgent 的差异**：
- CertAgent 用 `scp` 把**磁盘密文**传服务器 → 必然拿到密文 → 必须解密。
- 本项目是**浏览器网页上传**：用户在自己电脑选文件，DLP 透明加密通常在"读取"时
  自动解密，浏览器拿到的**多半是明文** → 传到服务器即明文，可能无需解密。
- **但不能假设**：若改用 scp/共享盘投递，或机器 DLP 策略不同，仍会是密文。

**对策（务实分级）**：
1. **上传即校验 magic bytes**（✅ 已实现）：`server.py` 的 `_upload` 读前 4 字节，
   非 `504B0304` 自动触发解密流程，无需用户手动操作。
2. **自动调解密服务兜底**（✅ 已实现）：新增 `cert_verify/decrypt.py` 模块，
   检测到加密文件自动 POST `http://10.18.87.51:8500/parse_file`（format=file），
   获取解密后的明文字节再交 openpyxl 正常解析。
   - 服务地址/开关/超时由环境变量控制：`DECRYPT_URL` / `DECRYPT_ENABLED` / `DECRYPT_TIMEOUT`
   - 解密服务不可用时给清晰提示"请本机 Excel 另存为 xlsx 再上传"
3. 前端 accept 已限 `.xlsx`；加密文件上传后用户无需感知解密过程（透明自动）。

**解密服务关键信息**：
- 地址：`http://10.18.87.51:8500/parse_file`
- 方式：POST form-data `file=上传文件 & format=file` → 返回解密后的文件流
- 支持类型：`.docx .doc .xlsx .xls .xlsm .pdf`
- 注意：解密依赖 Java 服务（libedi.jar），需服务器有 Java 17+ 环境

### 难点 4：中文文件名 / 编码
Excel 上传文件名常含中文。Linux 默认 UTF-8 一般没问题，但要确保
systemd 环境 `LANG=C.UTF-8` 或 `en_US.UTF-8`（借鉴 CertAgent converter 的 env 设置），
避免中文路径/文件名乱码。

---

## 四、建议的部署步骤（方案 A：先上 VDE+TÜV）

```bash
# 1. 系统依赖
sudo apt update && sudo apt install -y python3.11 python3.11-venv

# 2. 上传代码（解压 v2.6 交付包，或 git）
mkdir -p /home/hisense/certverify && cd /home/hisense/certverify
# 解压 证书核对工具_v2.6.zip 到此

# 3. 虚拟环境 + 依赖（VDE/TÜV 只需 openpyxl，标准库其余自带）
python3 -m venv venv && source venv/bin/activate
pip install -r cert_verify/requirements.txt

# 4. 网络自检：确认服务器能连三站
python -m cert_verify.cli netcheck

# 5. 配置 .env（如需代理）
echo 'CERT_PROXY=' > .env          # 直连留空；需代理则填 http://ip:port
echo 'LANG=en_US.UTF-8' >> .env

# 6. 配置 systemd（见第二节 service 文件），启动
sudo cp deploy/certverify.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now certverify

# 7. 放行端口
sudo ufw allow 8756/tcp

# 8. 验证
curl -I http://localhost:8756/
```

访问：`http://<服务器IP>:8756/`

> 需要 UL 时再按方案 B/C 扩展；先用 VDE+TÜV 把服务跑稳。

---

## 五、需要对代码做的小改动（上 Linux 前）

| 改动 | 文件 | 说明 | 优先级 |
|---|---|---|---|
| `serve()` 支持 `--host` 参数 | `web/__main__.py` / `server.py` | 服务器需绑 0.0.0.0 | 高 |
| 上传加密检测（magic bytes）| `web/server.py` `_upload` | 非 `504B0304` 给清晰提示，避免 openpyxl 晦涩报错 | 高 |
| UL channel 可配置（msedge/chromium）| `providers/ul.py` / `ul_login.py` | Linux 用自带 chromium | 中（做 UL 时）|
| UL 登录态改独立 profile + 持久化上下文 | `ul_login.py` | `data/ul-profile`，服务器重新生成 | 中（做 UL 时）|
| UL 账号密码走环境变量 | `ul_login.py` | `UL_USERNAME/UL_PASSWORD`，不写死 | 中（做 UL 时）|
| UL Cloudflare 验证 → 可恢复状态 | `tasks.py` / `ul.py` | Human-in-the-loop，暂停不狂刷 | 中（做 UL 时）|
| 解密服务兜底模块（可选）| 新增 `decrypt.py` | 若走 scp/共享盘投递密文时 | 低 |
| 加 `deploy/` 目录 | 新增 | certverify.service + nginx.conf（可选）+ 部署脚本 | 中 |
| 环境变量自动加载 dotenv | 可选 | 或全靠 systemd EnvironmentFile | 低 |

---

## 六、核心结论

1. **VDE + TÜV 上 Linux 无障碍**：纯 HTTP + openpyxl，跨平台，按方案 A 很快能跑起来。
2. **UL 是唯一难点**：Playwright+Cloudflare 在无头 Linux 上不确定，与 CertAgent 的
   LibreOffice/Word COM 是同类"环境边界"问题。上 Linux 的 UL 要点：
   自带 Chromium（非 Edge）→ 无头不行就 Xvfb 有头 → 登录态用持久化上下文做成独立模块 →
   账号密码走环境变量 → Cloudflare 验证走 Human-in-the-loop → 被风控优先官方API/合理频率/
   最后才 Windows Worker 隔离。
3. **文件加密要注意**：本机上传多为明文（DLP 读时解密），但若 scp/共享盘投递会是密文；
   上传处加 magic bytes 校验，必要时接解密服务兜底。
4. **可直接搬的经验**：systemd + EnvironmentFile、单进程内存态、端口一致、
   （可选 Nginx 只反代不读文件以避开 ACL 坑）、版本快照。
5. **我们比 CertAgent 幸运的地方**：没有 LibreOffice/.doc 这个最大的坑。
6. **务实路线**：先上 VDE+TÜV（覆盖大多数），UL 视需求再攻坚（Xvfb 或 Windows Worker）。

---

## 七、UL 上 Linux 的分步验证清单（真要做 UL 时按此走）

1. `python -m playwright install --with-deps chromium` 装浏览器+依赖
2. `xvfb-run -a python -c "from playwright.sync_api import sync_playwright; ..."` 测能否起 Chromium
3. xvfb 有头模式打开 UL 首页，人工过一次 Cloudflare + 登录，profile 存 `data/ul-profile`
4. 复用 profile 跑一条查询，看能否直接进结果页（不再要验证）
5. 切 headless=True 再跑一条，观察是否仍被 Cloudflare 拦
   - 稳定 → 用 headless
   - 不稳定 → 保持 Xvfb 有头，或转方案 C（Windows Worker）
6. 把查询间隔调大（如 8~15s + 抖动），观察长时间跑是否被风控
7. 全程用 Human-in-the-loop：检测到 Challenge 就暂停记录状态，人工处理后恢复
