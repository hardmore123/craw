# CertAgent 部署与使用指南

## 一、整体架构

```
同事浏览器(任意网段) → http://10.18.87.51:8123 → Nginx(:8123)
    ├── /            → 前端静态页面 (React SPA)
    ├── /api/*       → 后端 FastAPI (Uvicorn :8125)
    └── /health      → 后端健康检查
```

**⚠️ 文件格式限制**：服务器只支持 `.docx` 和 `.json` 格式。**不支持 .doc 自动转换**（LibreOffice 在 systemd headless 环境下不可靠，详见「部署问题排查日志」问题 11）。同事上传老 .doc 文件时需先用 Word 另存为 .docx。透明加密（DLP）的 .docx 会自动调解密服务还原，无需手动处理。

---

## 二、服务器部署步骤（Linux 10.18.87.51）

### 2.1 安装系统依赖

```bash
# SSH 登录服务器
ssh hisense@10.18.87.51

# 安装 Python 3.11
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip

# 安装 Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# 安装 Nginx
sudo apt install -y nginx

# 安装 LibreOffice（服务器上做 .doc → .docx 转换）
sudo apt install -y libreoffice
```

**注意**：LibreOffice 7.3 在 systemd headless 环境下不可靠（60s 超时），详见问题排查日志问题 11/12。建议**不要依赖服务器自动转换**，要求同事传 .docx 文件。如果必须支持 .doc 转换，可以尝试安装 xvfb 提供虚拟 display：`sudo apt install -y xvfb`（converter.py 已支持 xvfb-run 包装）。

### 2.2 上传项目代码

项目已上传至：`/home/hisense/pro/Gitlab/CertAgent`

### 2.3 部署

```bash
cd /home/hisense/pro/Gitlab/CertAgent

# 1. 创建虚拟环境 + 安装依赖
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 2. 配置 .env
cp .env.example .env
nano .env   # 填入 LLM 密钥等配置（见 2.4）

# 3. 构建前端
cd frontend
npm install
npm run build
cd ..

# 4. 创建数据目录
mkdir -p runs

# 5. 配置 Nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/certagent
sudo ln -sf /etc/nginx/sites-available/certagent /etc/nginx/sites-enabled/certagent
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# 6. 配置后端服务（开机自启）
sudo cp deploy/certagent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable certagent
sudo systemctl start certagent

# 7. 放行防火墙端口
sudo ufw allow 8123/tcp
```

### 2.3.1 关键权限设置（必做，nginx 500 的元凶）

`frontend/dist/` 目录虽然默认是 755，但末尾的 **`+` 表示有 ACL**，nginx worker（`www-data` 用户）默认读不到。

每次 `npm run build` 后必须执行：

```bash
sudo setfacl -R -m u:www-data:rx /home/hisense/pro/Gitlab/CertAgent/frontend/dist
sudo setfacl -m u:www-data:x /home/hisense \
    /home/hisense/pro/Gitlab \
    /home/hisense/pro/Gitlab/CertAgent \
    /home/hisense/pro/Gitlab/CertAgent/frontend
```

**验证**：`curl -i http://localhost:8123/` 应该返回 `HTTP/1.1 200 OK`，看到 index.html 内容（不是 nginx 500 错误页）。

部署完成后验证：
```bash
curl http://localhost:8125/health    # 后端直连
curl http://localhost:8123/health    # 经 Nginx 代理
sudo systemctl status certagent      # 查看服务状态
```

### 2.4 配置 .env（重要）

```bash
cd /home/hisense/pro/Gitlab/CertAgent
nano .env
```

需要填的关键项：

```bash
# LLM 接口（聚好看内网 higpt 网关）
LLM_BASE_URL=https://inner-apisix.hisense.com/higpt-new/v1
LLM_API_KEY=你的key
LLM_USER_KEY=你的user_key
LLM_MODEL=deepseek-v4-flash
SUGGESTER_MODE=llm

# Web 服务配置（生产环境建议改 SECRET_KEY）
SECRET_KEY=你的随机密钥字符串
DEFAULT_USER=admin
DEFAULT_PASSWORD=admin123

# 允许跨域
CORS_ALLOW_ORIGINS=http://10.18.87.51:8123,http://localhost:8123

# 长期记忆（人工决策反馈）落盘路径，默认 compare_core/memory/data/feedback.jsonl（git 追踪）
# 想集中管理可改成共享盘路径：FEEDBACK_FILE=/mnt/shared/feedback.jsonl
# FEEDBACK_FILE=
# MEMORY_ENABLED=true
# MEMORY_STRONG_MIN=2
```

改完后重启后端：
```bash
sudo systemctl restart certagent
```

---

## 三、前端使用说明

### 3.1 访问地址

同事浏览器打开：**http://10.18.87.51:8123**

### 3.2 登录

- 用户名：`admin`
- 密码：`admin123`

### 3.3 使用流程

```
登录 → 工作台 → 新建任务 → 上传两份清单 → 等待解析 → 差异确认 → 提交 → 下载产物
```

详细步骤：

#### Step 1：新建任务

在工作台页面点击"新建任务"，上传两份 Word 文件：

| 输入项 | 说明 | 示例 |
|--------|------|------|
| 文件 A（原始清单） | 旧版本/人工整理的关键件清单 | `4720467-85Z970QF_安全&EMC关键件清单.docx` |
| 文件 B（PLM新品） | PLM 导出的新品关键件清单 | `85ZXSF(01)_安全&EMC关键件清单.docx` |

可选勾选"LLM 智能配对"（对规则漏配的残余行做 AI 疑似匹配推荐）。

#### ⚠️ 重要：服务器不提供 .doc 自动转换

**只支持 .docx 格式**。如果手头是 .doc 格式（老 Word 文档），请先在 Word 中打开 → 「文件」→「另存为」 → 保存类型选「Word 文档 (.docx)」 → 再上传。

- 上传 .doc 文件会被前端 accept 拦截
- 即使改名后缀为 .docx（内容仍是 .doc 格式），也会被后端解析失败并给出清晰提示
- 另存为 .docx 一般只需几秒到几十秒

#### ⚠️ 关于加密文件（透明加密自动解密，Word 密码加密需手动解除）

系统已支持**透明加密（DLP）文件自动解密**：如果文件被公司安全软件透明加密（本机打开正常、但拷到别人电脑打不开），上传后系统会自动调解密服务还原，**无需手动操作**，直接上传即可。

但如果 .docx 文件被 Word **主动设置了打开密码**（文档保护加密），解密服务无法还原，需手动取消：

1. Word 打开加密的 .docx
2. 「文件」→「信息」→「保护文档」→「删除加密」（或选「始终以只读方式打开」）
3. 「文件」→「另存为」 → 保存类型选「Word 文档 (.docx)」 → 重新保存
4. 把新文件上传即可

**判断文件是否被 Word 加密**：文件管理器右键 → 属性 → 看文件类型，如果类型显示"Microsoft Word 文档 - 加密"，就是 Word 密码加密。

#### Step 2：等待解析

系统自动执行：
1. 解析表格内容（透明加密文件先自动调解密服务还原）
2. 归一化 + 匹配键对齐
3. 差异分类 + 置信度打分
4. 规则建议生成（可选 AI 智能配对）

进度条完成后自动跳转差异确认页。

#### Step 3：差异确认（核心页面）

并排 A|B 对照视图，逐行决策：

| 操作 | 含义 |
|------|------|
| ✅ 接受 | 采纳系统建议（新增/变更/保留） |
| ❌ 拒绝 | 丢弃该行，不写入最终清单 |
| 🔄 拒绝并匹配 | 当前配对不对，手动选另一行作为对应行 |
| ＝ 其实相同 | 系统误判为差异，实际是同一件（标记为无变更） |

每个决策可附加理由（进长期记忆，后续任务自动学习）。

#### Step 4：下载产物

确认提交后生成两份文档：
- **整合版关键件清单.docx** — 合并后的完整清单
- **变更前后对照表.docx** — 变更项的前后对比

点击下载按钮即可保存。

---

## 四、后端 API 使用说明

Swagger 文档：**http://10.18.87.51:8123/api/docs**

### 4.1 认证

所有 `/api/v1/*` 接口需要 Bearer Token：

```bash
# 登录获取 token
curl -X POST http://10.18.87.51:8123/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 返回: {"token": "eyJ...", "user": {"username":"admin","role":"admin"}}
```

后续请求加上 Header：`Authorization: Bearer eyJ...`

### 4.2 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/login` | 登录，获取 JWT |
| POST | `/api/v1/jobs` | 上传两份文件，创建比对任务 |
| GET | `/api/v1/jobs` | 查看所有任务列表 |
| GET | `/api/v1/jobs/{id}` | 查看单个任务状态 |
| GET | `/api/v1/jobs/{id}/diffs` | 获取差异列表（确认页数据源） |
| POST | `/api/v1/jobs/{id}/decisions` | 提交人工决策 |
| GET | `/api/v1/jobs/{id}/result/merged` | 下载整合版清单 |
| GET | `/api/v1/jobs/{id}/result/compare` | 下载变更对照表 |
| GET | `/api/v1/analytics` | 使用统计 |

### 4.3 curl 示例：创建任务

```bash
TOKEN="eyJ..."

curl -X POST http://10.18.87.51:8123/api/v1/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -F "file_orig=@原始清单.doc" \
  -F "file_plm=@PLM新品清单.doc" \
  -F "fuzzy_llm=false"
```

---

## 五、代码更新后重新部署

当你修改了代码并重新上传到服务器后：

```bash
# 方法 1：scp 覆盖上传（本地执行）
scp -r "C:\Users\likunyuan\Desktop\项目\CertAgent" hisense@10.18.87.51:/home/hisense/pro/Gitlab/

# 方法 2：GitLab 拉取（服务器执行）
cd /home/hisense/pro/Gitlab/CertAgent
git pull

# 然后在服务器上执行更新部署
cd /home/hisense/pro/Gitlab/CertAgent
sudo bash deploy/deploy.sh update
```

`update` 模式只做：安装新 Python 依赖 → 重建前端 → 重启后端，不动 Nginx 配置。

### 5.1 长期记忆路径迁移（2026-08-21 起）

长期记忆（人工决策反馈）已从 `runs/feedback.jsonl` 迁到 **git 追踪的** `compare_core/memory/data/feedback.jsonl`，避免服务器清 runs 目录时记忆丢失。

从旧版本升级需在服务器执行一次迁移：

```bash
cd /home/hisense/pro/Gitlab/CertAgent
mkdir -p compare_core/memory/data
mv runs/feedback.jsonl compare_core/memory/data/feedback.jsonl 2>/dev/null   # 无旧文件则跳过
sudo systemctl restart certagent
```

新路径在 git 追踪范围内，记忆随代码进版本库；要改到共享盘集中管理，在 .env 设 `FEEDBACK_FILE=你的路径`。

---

## 六、日常运维

### 查看后端日志
```bash
journalctl -u certagent -f        # 实时日志
journalctl -u certagent --since today  # 今天的日志
```

### 重启服务
```bash
sudo systemctl restart certagent   # 重启后端
sudo systemctl reload nginx        # 重载 Nginx 配置
```

### 查看服务状态
```bash
sudo systemctl status certagent
sudo systemctl status nginx
```

### 磁盘清理（任务产物）
```bash
# 任务数据存在 /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/
# 清理 30 天前的旧任务
find /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs -maxdepth 1 -mtime +30 -type d -exec rm -rf {} \;
```

### 长期记忆归档

记忆文件：`compare_core/memory/data/feedback.jsonl`（追加写，会持续增长）。

```bash
# 查看积累条数
wc -l /home/hisense/pro/Gitlab/CertAgent/compare_core/memory/data/feedback.jsonl

# 拉回本地（或运行项目根目录 _backup_certagent.ps1 时选 y 拉回）
scp hisense@10.18.87.51:/home/hisense/pro/Gitlab/CertAgent/compare_core/memory/data/feedback.jsonl .
```

- 进 git：拉回/本地积累后 `git add compare_core/memory/data/feedback.jsonl && git commit`
- 清空重来：备份后删除该文件即可（系统自动重建）
- 挖掘高频被否模式：`compare_core.memory.mine_reject_rules(min_rejects=2)`；别名对：`compare_core.memory.mine_aliases()`（详见「记忆库说明.md」）

### 关键任务调试命令

```bash
# 看最新任务的完整状态
LATEST=$(ls -t /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/ | head -1)
cat /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/$LATEST/job.json | python3 -m json.tool

# 关键日志：runner.py 的 [runner] 标记
sudo journalctl -u certagent --since "5 minutes ago" --no-pager | grep runner

# 清理 soffice 残留（用 TERM 不用 KILL）
pkill -TERM -f soffice 2>/dev/null
pkill -TERM -f Xvfb 2>/dev/null

# 修复 nginx 500 权限（每次 npm run build 后跑）
sudo setfacl -R -m u:www-data:rx /home/hisense/pro/Gitlab/CertAgent/frontend/dist
sudo setfacl -m u:www-data:x /home/hisense /home/hisense/pro /home/hisense/pro/Gitlab /home/hisense/pro/Gitlab/CertAgent /home/hisense/pro/Gitlab/CertAgent/frontend

# 验证 certagent.service 环境变量
systemctl show certagent -p Environment
# 期望看到 HOME=/home/hisense PATH=...
```

---

## 七、端口说明

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx（对外） | **8123** | 同事浏览器访问此端口 |
| Uvicorn（内部） | **8125** | 后端 API，仅 Nginx 反代访问，不对外暴露 |

---

## 八、常见问题

| 问题 | 解决方案 |
|------|----------|
| 访问 10.18.87.51:8123 白屏 | 检查前端是否构建: `ls /home/hisense/pro/Gitlab/CertAgent/frontend/dist/index.html` |
| 访问 10.18.87.51:8123 显示 nginx 500 默认错误页 | **大概率是 nginx worker 没读 dist 权限**（ACL 问题），见 2.3.1 节，跑 setfacl 命令 |
| 浏览器控制台报 favicon.ico 500 | nginx.conf 应含 `location = /favicon.ico { return 204; }`，如缺失需重新部署 nginx.conf |
| 上传文件报 413 | nginx.conf 已设 50m 限制，超大文件需调大 `client_max_body_size` |
| 解析超时 | nginx.conf 已设 300s，大文件可改更大后 `sudo systemctl reload nginx` |
| 后端 502 | `sudo systemctl status certagent` 看是否崩溃，日志排查 |
| LLM 调用失败 | 检查 .env 中 LLM_* 配置，确认服务器能访问聚好看内网网关 |
| 上传 .doc 文件被拒绝 | **预期行为**：服务器只支持 .docx。同事用 Word 打开 → 「另存为 .docx」→ 再上传 |
| 上传 .docx 但任务 diff_count = 0 | 先看是否**透明加密**（系统自动调解密服务，一般能还原）。若仍是 **Word 密码加密**（解密服务也解不了，`file` 显示 `data`、python-docx 抛 PackageNotFoundError），**正确做法**：Word 打开 → 「文件→信息→保护文档→删除加密」→ 另存为 .docx 再传（详见 Step 1 加密说明） |
| 任务报 `文件 xxx.docx 不是合法的 .docx 格式` | 可能是 Word 密码加密、损坏或非 Word 文档。透明加密文件会自动调解密服务；解密也失败时，换台电脑/文件确认是真 .docx，再看后端日志定位根因 |
| 任务报 `无法把 .doc 转换为 .docx` | 见部署问题排查日志「问题 11/12」，LibreOffice 7.3 在 systemd 下不可靠，已改用方案 A + runner 兜底 |
| 其他网段无法访问 | 检查防火墙: `sudo ufw status` 或 `iptables -L`，确认 8123 端口放行 |

### 重要：权限与端口

- **每次 `npm run build` 后都要跑 setfacl**（2.3.1 节），否则 nginx 返 500
- **服务端口固定为 8123/8125**，所有文档/前端/反代都要保持一致
- **文件格式**：只接受 .docx 和 .json。.doc 必须先在 Word 另存为 .docx

---

## 九、未来改进建议

### 短期（1 周内）
1. **生产环境 .env SECRET_KEY 改成 ≥32 字节**（消除 jwt 警告）
2. **评估撤回 webapi/app.py 的 .doc 拦截**（如果未来 xvfb 方案能跑通）

### 中期（1 月内）
1. **升级 LibreOffice 版本**：Ubuntu 24.04 仓库的 LibreOffice 24+ 修复了 7.3 多个 bug
2. **或换 ONLYOFFICE**（Linux 原生 Office，Docker 部署）

### 长期（季度内）
1. **评估 docx2pdf + python-docx 直接生成路线**（不依赖 LO）
2. **CI/CD 自动化部署**（避免手动 scp + 重启）

---

## 十、关联文档

- [部署问题排查日志](部署问题排查日志.md) — 21 个问题的详细分析与 5 个失败方案对比
- [README.md](README.md) — 部署概述（端口已统一为 8123/8125）
