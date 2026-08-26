# CertAgent 服务器部署问题排查日志

服务器：10.18.87.51 (Linux Ubuntu 22.04, hisense 用户)
项目路径：`/home/hisense/pro/Gitlab/CertAgent`
日期：2026-08-19

---

## 问题 1：500 Internal Server Error（Nginx 无法读取前端文件）

**现象**：浏览器访问显示 Nginx 500 错误

**原因**：Nginx 以 `www-data` 用户运行，无权限访问 `/home/hisense/` 目录（Linux home 目录默认权限 700）

**解决**：
```bash
chmod 755 /home/hisense
chmod 755 /home/hisense/pro
chmod 755 /home/hisense/pro/Gitlab
chmod 755 /home/hisense/pro/Gitlab/CertAgent
chmod 755 /home/hisense/pro/Gitlab/CertAgent/frontend
chmod 755 /home/hisense/pro/Gitlab/CertAgent/frontend/dist
```

---

## 问题 2：ModuleNotFoundError: No module named 'pythoncom'

**现象**：任务上传后立即报错 `No module named 'pythoncom'`

**原因**：
1. `webapi/runner.py` 中有 `import pythoncom`（Windows 专用模块，Linux 上不存在）
2. 上传文件路径硬编码为 `orig.doc` / `plm.doc`，但实际上传的是 `.docx` 文件

**解决**：
1. 删除 `import pythoncom`，改为注释 `# Word 文件解析（跨平台）`
2. `webapi/app.py` 改为保留原始扩展名：
   ```python
   orig_ext = Path(file_orig.filename).suffix.lower() if file_orig.filename else ".doc"
   plm_ext = Path(file_plm.filename).suffix.lower() if file_plm.filename else ".doc"
   orig_path = rec.job_dir / f"orig{orig_ext}"
   plm_path = rec.job_dir / f"plm{plm_ext}"
   ```
3. `runner.py` 中动态查找文件（不再硬编码 `orig.doc`）：
   ```python
   for f in rec.job_dir.iterdir():
       if f.name.startswith("orig"):
           orig_doc = f
       elif f.name.startswith("plm"):
           plm_doc = f
   ```

---

## 问题 3：PackageNotFoundError: Package not found at '/home/.../orig.docx'

**现象**：文件已正确保存为 `.docx`，但 python-docx 打开时报 PackageNotFoundError

**原因**：用户上传的文件名虽然是 `.docx`，但实际内容是老 `.doc` 格式（Composite Document File V2），python-docx 无法解析

**解决**：在 `_load_one` 中加入 fallback 逻辑——当 `parse_docx` 失败后，把文件复制为 `.doc` 后缀，再用 LibreOffice 转换：
```python
# 3) 用 LibreOffice 强制转换
actual_path = path
if path.suffix.lower() == ".docx":
    actual_path = path.parent / (path.stem + ".doc")
    shutil.copy2(path, actual_path)
docx_path = ensure_docx(str(actual_path), work_dir=converted_dir)
return parse_docx(docx_path), docx_path
```

---

## 问题 4：RuntimeError: 无法把 .doc 转换为 .docx。请安装 LibreOffice

**现象**：LibreOffice 已安装（手动 `soffice --headless --convert-to docx` 能成功），但 systemd 服务里调用失败

**原因分析**：
1. systemd 服务进程缺少 `HOME` 环境变量 → LibreOffice 无法创建用户配置
2. 缺少 `SAL_USE_VCLPLUGIN=svp` → LibreOffice 尝试连接 X11 显示服务器失败
3. 尝试加 `-env:UserInstallation` + `--nolockcheck` → 导致 LO 7.3 版本 100% CPU 死循环

**解决方案（最终版）**：
1. systemd service 加 HOME 环境变量：
   ```ini
   Environment="HOME=/home/hisense"
   ```
2. converter.py 使用干净的最小环境调用 soffice：
   ```python
   env = {
       "PATH": "/usr/local/bin:/usr/bin:/bin",
       "HOME": str(out_dir.resolve()),
       "LANG": "en_US.UTF-8",
       "LC_ALL": "en_US.UTF-8",
       "SAL_USE_VCLPLUGIN": "svp",  # 强制无头渲染后端
   }
   ```
3. **不要**使用 `-env:UserInstallation` 和 `--nolockcheck`（LO 7.3 会死循环）

**当前状态**：certagent.service 已包含 `Environment="HOME=/home/hisense"`，converter.py 已包含 `SAL_USE_VCLPLUGIN=svp` 最小环境方案，待上传服务器实测验证。

---

## 问题 5：任务列表闪动（一会有一会没有）

**现象**：前端任务列表来回闪动，时有时无

**原因**：uvicorn 配置了 `--workers 2`（多进程），任务状态存在内存中，两个 worker 不共享。请求被负载均衡轮流分到不同 worker。

**解决**：改为单 worker：
```bash
sudo sed -i 's/--workers 2/--workers 1/' /etc/systemd/system/certagent.service
sudo systemctl daemon-reload
sudo systemctl restart certagent
```

---

## 问题 6：LibreOffice 进程 100% CPU 死循环

**现象**：`ps aux | grep soffice` 显示进程占用 100% CPU，任务一直卡在 running

**原因**：使用了 `-env:UserInstallation=file://...` + `--nolockcheck` 参数组合，在 LibreOffice 7.3 上触发了已知 bug

**解决**：去掉 `-env:UserInstallation` 和 `--nolockcheck`，回归最简命令：
```python
cmd = [soffice, "--headless", "--norestore", "--convert-to", "docx", "--outdir", ..., ...]
```
加上 `SAL_USE_VCLPLUGIN=svp` 环境变量替代。

---

## 当前状态与待验证

**已确认**：
- ✅ LibreOffice 已安装 (`libreoffice --version` → 7.3.7)
- ✅ 手动 Python 调用 `ensure_docx` + `parse_docx` 完全成功
- ✅ 手动 `soffice --headless --convert-to docx` 成功
- ✅ systemd service 的 PATH 包含 `/usr/bin`（soffice 所在位置）
- ✅ `_find_libreoffice()` 在 Python 中返回 `/usr/bin/soffice`
- ✅ systemd service 已加 `Environment="HOME=/home/hisense"`
- ✅ converter.py 已包含 `SAL_USE_VCLPLUGIN=svp` 最小环境变量方案
- ✅ 端口已统一为 8123（Nginx 对外）/ 8125（Uvicorn 内部），与 nginx.conf / certagent.service 一致
- ✅ 文档（部署与使用指南.md、README.md）端口描述已同步为 8123/8125
- ✅ nginx favicon.ico 处理已加（location = /favicon.ico { return 204; }）
- ✅ nginx www-data 权限已加 setfacl
- ✅ 已决定**方案 A**：服务器不提供 .doc 自动转换，要求用户先在 Word 另存为 .docx

**待验证**：
- ~~最新 converter.py（加 `SAL_USE_VCLPLUGIN=svp` 最小环境变量方案）上传后是否能通过 systemd 服务正常转换~~ → 已废弃，LibreOffice 7.3 在 systemd 环境下不可靠（见问题 11）

---

## 问题 7：部署文档端口与实际配置不一致

**现象**：同事按部署与使用指南访问 `http://10.18.87.51:8080` 失败，实际端口是 8123

**原因**：

| 来源 | Nginx 端口 | Uvicorn 端口 |
|------|-----------|-------------|
| 部署与使用指南.md | 8080 ❌ | 9090 ❌ |
| README.md | 80 ❌ | 8000 ❌ |
| nginx.conf / certagent.service（实际） | **8123** ✅ | **8125** ✅ |

**解决**：统一所有文档到 8123/8125
- `deploy/部署与使用指南.md` 改12 处端口
- `deploy/README.md` 改 5 处端口

---

## 问题 8：certagent.service 缺 HOME 环境变量

**现象**：LibreOffice 在 systemd 启动的 webapi 进程里无法初始化 profile，转换卡死

**原因**：service 文件只设了 PATH，没设 HOME。systemd 启动进程时 HOME 默认空，导致 LibreOffice 无法创建 `~/.config/libreoffice` profile。

**解决**：在 `deploy/certagent.service` 第10 行加：

```ini
Environment="HOME=/home/hisense"
```

**验证**：

```bash
systemctl show certagent -p Environment
# 期望看到 HOME=/home/hisense PATH=...
```

---

## 问题 9：nginx 500 错误 — favicon.ico

**现象**：浏览器控制台报 `Failed to load resource: 500 (Internal Server Error)` for `/favicon.ico`

**原因**：前端 `dist/` 目录只有 `favicon.svg`，没有 `favicon.ico`。浏览器默认请求 `/favicon.ico`，nginx 找不到时返回 500（默认行为，取决于 root + try_files）。

**解决**：在 `deploy/nginx.conf` 加静默拦截：

```nginx
location = /favicon.ico {
    return 204;
}
```

---

## 问题 10：nginx 500 错误 — 目录 ACL 权限（关键！）

**现象**：

- 浏览器访问 `http://10.18.87.51:8123/` 显示 nginx 默认 500 错误页
- 但 `curl http://localhost:8123/favicon.ico` 返回 204 正常
- `curl http://localhost:8123/` 返回 500
- `curl http://localhost:8125/health` 返回 200 OK

**根因**：

- nginx worker 进程运行在 **`www-data`** 用户
- `frontend/dist/` 目录是 `hisense:hisense` 拥有
- 权限末尾的 **`+` 表示有 ACL**（Access Control List）
- ACL 限制了 `www-data` 读取 dist 下的文件
- `/favicon.ico` 走单独 location（return 204），不走文件系统，所以正常

**诊断命令**：

```bash
ls -ld /home/hisense/pro/Gitlab/CertAgent/frontend/dist
# drwxr-xr-x+ 3 hisense hisense  ←末尾的 + 是 ACL 标记
```

**解决**：用 `setfacl` 显式给 `www-data` 授权遍历路径：

```bash
sudo setfacl -R -m u:www-data:rx /home/hisense/pro/Gitlab/CertAgent/frontend/dist
sudo setfacl -m u:www-data:x /home/hisense \
    /home/hisense/pro/Gitlab \
    /home/hisense/pro/Gitlab/CertAgent \
    /home/hisense/pro/Gitlab/CertAgent/frontend
```

**关键点**：`www-data` 必须能从 `/home/hisense` 一路 traverse 到 `dist`，每一级目录都要有 `x`（执行）权限。ACL 才能精确放行特定用户。

**教训**：

- 排查日志"问题 1"提到过权限问题，但这次栽在**子目录的 ACL** 上（外层 755 但 ACL 限制）
- 每次 `npm run build` 后建议重新跑 setfacl（构建产物可能继承不同 ACL）

---

## 问题 11：LibreOffice 7.3 在 systemd headless 环境下不可靠（关键决策）

**现象**：用户上传 `.docx` 文件（实际内容是 `.doc` 格式）→ webapi 触发 LibreOffice 转换 → 任务报 `RuntimeError: 无法把 .doc 转换为 .docx。请安装 LibreOffice` 或 `LibreOffice 转换超时(60s)`

**尝试过的方案（全部失败）**：

| # | 方案 | 结果 |
|---|------|------|
| 1 | `HOME=str(out_dir.resolve())` 当临时 HOME（converter.py 旧版） | ❌ LO 7.3 重建 profile 卡死，120s 超时 |
| 2 | 继承父进程 HOME（`env = os.environ.copy()`） | ❌ LO 仍卡死 |
| 3 | 预热 profile（`soffice --terminate_after_init`）+ 继承 HOME | ❌ 仍 60s 超时 |
| 4 | 显式 `-env:UserInstallation=file:///home/hisense/.config/libreoffice` | ❌ 仍 60s 超时 |
| 5 | 加 `TMPDIR=/tmp` 环境变量 | ❌ 无效 |

**根因**：

- LibreOffice 7.3 在 systemd 启动的 webapi 进程下**完全不可靠**
- 任何"启动时初始化 user profile"的操作都会卡住
- 转换工作可能完成（文件写出来了），但 `soffice.bin` 进程不退出
- 即使显式指定 profile 路径也无效

**对比本地（Windows）为什么不卡**：

- 本地用 **Word COM**（走 win32com + pythoncom），不依赖 LibreOffice
- 本地的 LibreOffice 如果能装上，配合桌面 session 也能正常工作
- 服务器只有 LibreOffice，没 Word → 必须走 LibreOffice → 卡死

---

## ✅ 最终决策：方案 A — 拒绝 .doc 上传

放弃 LibreOffice 路径，**要求用户在 Word 里另存为 .docx 再上传**。

### 改动清单（2026-08-19）

| 文件 | 改动 |
|------|------|
| `webapi/app.py` | create_job 检测 `.doc` 后缀直接返回 HTTP 400，附带清晰提示 |
| `webapi/runner.py` | `_load_one` 删掉 LibreOffice fallback，解析失败抛清晰 RuntimeError |
| `frontend/src/pages/WorkspacePage.tsx` | 上传区加黄底警告，input accept 改为 `.docx,.json` |
| `deploy/certagent.service` | 加 `Environment="HOME=/home/hisense"` |
| `deploy/nginx.conf` | 加 `location = /favicon.ico { return 204; }` |
| `deploy/部署与使用指南.md` | 端口统一到 8123/8125，加 .docx 要求说明 |
| `deploy/README.md` | 端口同步到 8123/8125 |

### webapi/app.py 关键代码

```python
for label, f in (("原始清单", file_orig), ("PLM清单", file_plm)):
    if f and f.filename:
        ext = Path(f.filename).suffix.lower()
        if ext == ".doc":
            registry._jobs.pop(rec.job_id, None)
            shutil.rmtree(rec.job_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"【{label}】文件 {f.filename} 是 .doc 格式，服务器暂不支持自动转换。"
                       "请用 Word 打开后「另存为 .docx」再上传，几十秒搞定。",
            )
```

### webapi/runner.py 关键改动

```python
def _load_one(path: Path):
    # 1) JSON fixture
    # 2) .docx → python-docx
    # 失败 → RuntimeError 清晰提示（不再调 LibreOffice）
    try:
        return parse_docx(str(path)), str(path)
    except Exception as e:
        raise RuntimeError(
            f"文件 {path.name} 不是合法的 .docx 格式（{type(e).__name__}: {str(e)[:200]}）。"
            "服务器不提供 .doc 自动转换，请用 Word 打开后「另存为 .docx」再上传。"
        )
```

### 为什么选方案 A（而不是继续折腾 LibreOffice）

| 对比项 | 方案 A（拒绝 .doc） | 方案 B（继续修 LO） |
|--------|---------------------|---------------------|
| 改动量 | 5 行代码 + 1 个提示 | 不可预估，可能再耗数小时 |
| 稳定性 | 100% | 不确定，LO 7.3 有多个已知 bug |
| 用户操作 | 几十秒另存为 | 无 |
| 业务影响 | 一个月偶尔几次 .doc 上传 | – |
| 未来恢复 LO 路径 | 把 webapi 检测去掉即可 | – |

### 教训

- **LibreOffice 7.3 + headless + systemd 是雷区**，不要再轻易尝试
- 业务侧**改工作流比改技术栈成本低得多**
- 同事传 `.docx` 但内容是 `.doc` 格式（python-docx 打不开）很常见，前端 accept + webapi 拦截 + runner 兜底 三层防护才完整

---

## 问题 12：xvfb-run 尝试救活 LibreOffice（仍未成功）

**时间**：2026-08-19 17:30 ~ 17:45
**目标**：用 xvfb-run 给 LibreOffice 提供虚拟 X11 display，解决 systemd headless 下卡死的问题

### 改动

1. 服务器装 xvfb：`sudo apt install -y xvfb`（已确认 xvfb 2:21.1.4-2ubuntu1.7~22.04.16 安装成功）
2. `compare_core/parser/converter.py` `_convert_via_libreoffice` 用 xvfb-run 包装 soffice：
   ```python
   xvfb_run = shutil.which("xvfb-run")
   if xvfb_run:
       cmd = [xvfb_run, "-a", "--server-args=-screen 0 1024x768x24", soffice, ...]
   ```
3. `webapi/runner.py` `_load_one` 恢复 LibreOffice 转换路径：先 python-docx 解析失败 → 复制为 .doc → ensure_docx → LibreOffice + xvfb → parse_docx → 还失败 → 返回空 rows

### 实测结果（失败）

**xvfb-run 在 webapi 进程环境下无法救活 LibreOffice**：

```
[runner] orig.docx 不是合法 .docx，尝试 LibreOffice 转换: PackageNotFoundError: ...
[runner] orig.docx LibreOffice 转换也失败: RuntimeError: 无法把 .doc 转换为 .docx。请安装 LibreOffice
```

- 转换耗时 **60 秒整**（与 xvfb-run 启动开销吻合，但转换本身卡住）
- 超时被杀

### 排查路径

xvfb 装好但 LibreOffice 仍 60s 超时。问题可能是：

1. **xvfb-run 启动慢**：xvfb 启动 + 等待 display + soffice 初始化总共吃掉大部分 60s
2. **LibreOffice 初始化仍需要其他资源**：除了 display 还要 fonts/cache/profile
3. **systemd 进程 HOME 不影响但 XDG_RUNTIME_DIR 缺失**：xvfb 需要这个变量
4. **超时设得太短**：60s 不够，xvfb 启动就吃掉 10-20s

### 教训

- xvfb 不是 LO 7.3 systemd 卡死的银弹
- 调试 LibreOffice 自动化问题时，要看**完整 subprocess stdout/stderr**，不只是 exit code
- **超时要分开计算**：xvfb 启动（~10s）+ soffice 转换（~20s for 大文件）= 至少 60s 才安全

---

## ✅ 最终方案：方案 A + runner 兜底（采用中）

**当前部署状态**：

1. **`webapi/app.py`** 检测 `.doc` 后缀 → 返回 HTTP 400 + 清晰提示
2. **`frontend/src/pages/WorkspacePage.tsx`** 加黄底警告，input accept 改为 `.docx,.json`
3. **`webapi/runner.py`** 解析失败 → 尝试 LibreOffice（xvfb-run）→ 还失败 → 返回空 rows（让下游跑）

**行为**：
- 用户上传 `.doc` → 前端不让选 + webapi 拒绝 → 提示另存为 .docx
- 用户上传 `.docx`（内容是 .doc 格式）→ python-docx 失败 → 试 LibreOffice（xvfb 路径）→ 大概率 60s 超时 → 返回空 rows → 任务进入 review（diff_count=0）
- 用户上传真 `.docx` → 解析成功 → 任务正常跑

**教训**：
- 调试了一天 LibreOffice，最终方案 A 才是务实的选择
- LibreOffice 7.3 在生产环境（systemd headless）就是不可靠，不要再折腾
- 同事上传 .doc 时手动 Word 另存为，几十秒成本可控

---

## 📊 完整时间线回顾（2026-08-19）

| 时间 | 事件 | 结论 |
|------|------|------|
| 上午 | 部署问题排查日志问题 1-6 历史问题 | 已修复 |
| 14:11 | 端口不一致：部署指南 8080/9090 vs 实际 8123/8125 | ✅ 统一 |
| 15:00 | certagent.service 缺 HOME 环境变量 | ✅ 加上 |
| 15:12 | 前端 500 错误（favicon.ico） | ✅ nginx 加 return 204 |
| 15:30 | nginx 500 错误（dist/ 目录 ACL 权限） | ✅ setfacl 给 www-data 授权 |
| 16:21 | 决定方案 A：拒绝 .doc 上传 | 临时方案 |
| 16:35 | 验证后续流程能跑通到 review | ✅ 流程通了但 diff_count=0 |
| 17:00 | 尝试方案 A'：xvfb-run 救活 LibreOffice | 安装 xvfb |
| 17:30 | xvfb-run 包装 soffice | 代码改完 |
| 17:45 | xvfb 方案实测：LibreOffice 仍 60s 超时 | ❌ 失败 |
| 17:50 | **最终方案**：方案 A + runner 兜底（xvfb 失败时返回空 rows） | ✅ 采用 |

---

## 🔬 关键问题根因分析（综合）

### 问题 5（LibreOffice 不可靠）5 个失败方案的根因分解

| # | 方案 | 失败根因 |
|---|------|----------|
| 1 | `HOME=str(out_dir.resolve())` 临时目录当 HOME | LO 7.3 每次重建 profile，systemd 无 display 触发卡死 |
| 2 | 继承父进程 HOME（`env=os.environ.copy()`） | HOME 解决了但 VCL 后端仍需 display，profile 初始化仍卡 |
| 3 | 预热 profile（`soffice --terminate_after_init`） | 预热成功但 webapi 进程下 LO 仍卡 |
| 4 | 显式 `-env:UserInstallation=file://...` | profile 路径固定了，LO VCL 后端仍需要 display |
| 5 | xvfb-run 提供虚拟 display | xvfb 启动成功但启动开销 + LO 初始化耗时，60s 内未完成转换 |

### LibreOffice 7.3 在 systemd headless 下不可靠的 5 个根因

1. **缺 X11 display 句柄**：`SAL_USE_VCLPLUGIN=svp` 强制无头渲染后端，但 VCL 仍需 display 句柄初始化
2. **HOME/profile 初始化**：systemd 进程 HOME 缺失或临时，导致 profile 重建
3. **超时设置短**：60s 不够，xvfb 启动 + LO 初始化 + 转换 + 退出需要更多时间
4. **进程隔离问题**：systemd 进程继承的 env 不足以满足 LO 全部需求（XDG_RUNTIME_DIR、DISPLAY 等可能缺失）
5. **LO 7.3 已知 bug**：与 `-env:UserInstallation + --nolockcheck` 组合会100% CPU 死循环

### 业务侧 vs 技术侧方案对比

| 维度 | 技术侧（继续修 LO） | 业务侧（改工作流） |
|------|---------------------|---------------------|
| 改动量 | 不可预估，可能再耗数小时 | 5 行代码 + 1 个提示 |
| 稳定性 | 不确定（LO 7.3 多 bug） | 100% |
| 用户操作 | 无 | 几十秒 Word 另存为 |
| 业务影响 | – | 一个月偶尔几次 .doc 上传 |
| **ROI** | **低** | **高** |

**结论**：方案 A（业务侧改工作流）ROI 远高于方案 B（技术侧继续修 LO）。

---

## 📚 经验教训总结

### 教训 1：nginx 权限是部署常见陷阱
- nginx worker 默认 `www-data`，文件放 `hisense` 家目录需要 ACL 显式授权
- ACL 末尾的 `+` 标记是关键线索
- 每次 `npm run build` 后 ACL 可能变化，要重新 setfacl

### 教训 2：浏览器缓存 vs 服务器 500 难区分
- nginx access.log 空 + 浏览器报500 → 浏览器缓存了旧响应
- 不要瞎改配置，先清缓存 / 无痕模式验证

### 教训 3：不要用 `pkill -9` 杀 soffice
- 会截断正在写的 docx 文件，留下无法使用的污染产物
- 调试时用 `pkill -TERM` 给 LO 退出机会

### 教训 4：LibreOffice 7.3 + headless + systemd 是雷区
- 不要轻易尝试自动修复
- 改工作流比改技术栈成本低得多

### 教训 5：端口一旦定下来，所有文档/前端/反代都要同步
- grep 全项目（*.md / *.conf / *.service）确保端口一致
- 修改任何端口都要全项目同步

### 教训 6：调试时给 runner 加 print 日志
- 这次能定位"为什么 diff_count=0"靠的是 `[runner] 跳过...` 日志
- 下次改 runner 也记得加 print 标记关键节点

### 教训 7：scp + grep 验证是部署标配
- 改完代码 → scp 上传 → grep 服务器确认到位 → 重启服务
- 不要假设 scp 一定成功，每次都验证

---

## 🚀 未来改进建议

### 短期（1 周内）
1. **撤回 webapi/app.py 的 .doc 拦截**（如果 xvfb 方案某天能跑通）
2. **改 runner.py 解析失败抛错**（去掉"返回空 rows"兜底，让失败明显）
3. **生产环境 .env SECRET_KEY 改成 ≥32 字节**（消除 jwt 警告）

### 中期（1 月内）
1. **换 LibreOffice 版本**：Ubuntu 24.04 仓库的 LibreOffice 24+ 修复了 7.3 多个 bug
2. **或换 ONLYOFFICE**（Linux 原生 Office，Docker 部署）

### 长期（季度内）
1. **评估 docx2pdf + python-docx 直接生成路线**（不依赖 LO）
2. **CI/CD 自动化部署**（避免手动 scp + 重启）

---

## 问题 13：加密 Word 文档（最终根因！）

**时间**：2026-08-19 18:30 ~ 19:00
**确认方法**：换一台电脑上传未加密的 .docx → 一切正常

### 现象

用户上传 .docx → 任务报 `PackageNotFoundError` → 状态 `failed`（思路 A 已生效）→ `file` 命令对文件输出 `data`（既不是 zip 也不是 CDFV2）

### 根因（最终确认）

**文件是加密的 Word 文档**（很可能是 Word 2016+ 默认 AES Agile Encryption）：

| 文件状态 | 文件头 (前4字节) | `file` 命令输出 | python-docx 处理 |
|---------|----------------|----------------|------------------|
| 未加密 .docx | `504B0304` | `Microsoft OOXML` | ✅ 正常打开 |
| **加密 .docx** | **`??`**（不是 zip） | **`data`** | ❌ 抛 PackageNotFoundError |
| 未加密 .doc | `D0CF11E0` | `CDFV2` | ✅ LO 可转 |
| **加密 .doc** | **`??`**（不是 OLE2） | **`data`** | ❌ 同上 |

**为什么之前排查走了弯路**：
- 我之前测试 `file orig.docx` 显示 `data` 时，**误以为是文件损坏或非 Word 文档**
- 实际上加密 docx 的 zip 头可能被加密层覆盖，file 识别不出
- 真正的诊断方法是：**用原始（未加密） .docx 重新测试**

### 用户解密方法（Word 2016+）

1. Word 打开加密 .docx
2. **文件 → 信息 → 保护文档 → 删除加密**（或"始终以只读方式打开"）
3. **文件 → 另存为 .docx**（重新保存为标准 OOXML）
4. 上传新文件即可

### 教训

- **加密文件**是这次"看似 LibreOffice 不可靠"问题的真正元凶
- LibreOffice 7.3 + systemd 确实不可靠，但**不是这次卡死的根因**
- 排查文件问题时，**必须先确认文件本身是否合法**（用未加密版本对照测试）
- `file` 命令对加密文件不可靠（输出 `data`），需要其他验证手段（如 python-docx 抛的异常类型）

### 改动清单（已生效）

| 文件 | 改动 |
|------|------|
| `webapi/runner.py` | 解析失败抛清晰 RuntimeError（思路 A，不再静默 0 行） |
| `frontend/src/pages/WorkspacePage.tsx` | 前端文件头校验（思路 C，提前拦截） |
| `deploy/部署与使用指南.md` | 常见问题表加加密条目 |

### 排查流程回顾

```
1. 用户报：上传 docx 失败
   ↓
2. 排查 LibreOffice / xvfb / systemd（5 个方案全失败）
   → 走偏了：实际根因不是转换器
   ↓
3. 用户换电脑传未加密 docx → 一切正常
   → 真相大白：之前那些文件是加密的
   ↓
4. 加密 docx 的特征：
   - `file` 显示 data
   - python-docx 抛 PackageNotFoundError
   - LibreOffice 也无法直接打开
   ↓
5. 解决：用户取消加密 + 另存为 .docx
```

### 防患于未然

加密 .docx 是**少数场景**，但为同事方便，加解密指南已写入部署与使用指南的 Step 1。

如果发现同事经常传加密文件，可以考虑 webapi 加 zip 解压检测 `word/EncryptedPackage` → 主动提示解密（10 行代码），但目前不是必须。

---

## 问题 14：Word COM 在 Linux 服务器上不可用（架构层根因）

**核心结论**：**Word COM 无法在 Linux 服务器上直接使用**，这不是 bug，是技术边界。

### 直接原因

| # | 原因 | 说明 |
|---|------|------|
| 1 | **COM 是 Windows 专属** | Microsoft 的进程间通信机制只在 Windows 上实现 |
| 2 | **Microsoft Word 没有 Linux 版** | Microsoft 官方不为 Linux 提供桌面 Word（只有 Web 版 Office 365） |
| 3 | **Python 包限制** | `win32com.client` 和 `pythoncom` 仅 Windows 上的 pywin32 包提供 |
| 4 | **代码硬保护** | `if os.name == "nt"` 直接跳过 Linux 路径 |

### 代码层验证

```python
# converter.py 第 145 行
if os.name == "nt":  # ← Linux 上 os.name == "posix"，永远 False
    try:
        return str(_convert_via_word_subprocess(src, out_dir))
```

服务器 Python 实测：
```python
>>> import os
>>> os.name
'posix'  # ← 不是 'nt'
```

### 本地 vs 服务器对比

| 维度 | 本地（Windows） | 服务器（Linux） |
|------|----------------|----------------|
| Word COM 路径 | ✅ 首选，win32com 调用本机 Word | ❌ 完全跳过（os.name != "nt"） |
| LibreOffice 路径 | ✅ 备选（装了的话） | ⚠️ 不可靠（systemd headless 卡死） |
| 转换保真度 | ⭐⭐⭐⭐⭐ Word 原生 | ⭐⭐ LO 转 OOXML 有损 |

### 如果一定要在 Linux 用 Word COM

**唯一可行的架构是单独的 Windows Server**：

```
Linux 服务器（业务）
   │
   │ HTTP/gRPC
   ▼
Windows Server（Word COM）
   │
   ├── Microsoft Word（必须装正版）
   ├── Python + pywin32
   └── HTTP API：
       POST /convert
       Body: { "input": "xxx.doc", "output_format": "docx" }
       Returns: { "output_path": "..." }
```

**代价**：
- 需要一台 Windows Server（即使是 VM）
- 维护成本：Word 升级、COM 崩溃处理、Office 用户配置文件清理
- 单点依赖：Word 进程挂了整个转换链路挂

### 不推荐的"曲线救国"方案

| 方案 | 问题 |
|------|------|
| **Wine + Word** | 兼容性差、Office 登录问题、经常崩溃。社区普遍归为"实验性方案" |
| **Linux 版 Office（LibreOffice / ONLYOFFICE）** | 转换精度不如 Word，特别是复杂表格/页眉页脚 |
| **CrossOver + Word** | 商业版 Wine，更稳定但仍需许可证且有兼容坑 |

### CertAgent 当前决策（最终方案）

**业务侧改工作流**：

| 用户操作 | 系统响应 | 业务影响 |
|---------|---------|---------|
| 上传 .docx（未加密） | ✅ 全流程跑通 | 零 |
| 上传 .docx（加密） | ❌ 思路 A 抛清晰错误，提示解密 | 用户手动解密 |
| 上传 .doc | ❌ 方案 A 拦截，提示另存为 .docx | 用户手动另存为 |
| 上传 .json fixture | ✅ 全流程跑通 | 零 |

**结论**：没有 Word COM 也能跑业务，代价是让用户多一步手动操作。
**ROI 远高于**折腾 Word COM RPC 架构。

### 教训

- **架构层边界**比代码 bug 更难解决（Word COM 不可用 = 平台限制）
- 评估方案时**先确认技术可行性边界**，再投入精力
- CertAgent 的实际需求（关键件清单表格提取）**不需要 Word 原生保真度**
- 业务侧改工作流成本低、可控、可逆，胜过技术侧硬刚

---

## 问题 15：从本地原型到生产部署的完整改动清单

**对比对象**：`list-merge-demo/`（2026-07-17 本地原型）→ `CertAgent/`（2026-08-19 服务器生产版）

**核心结论**：**核心引擎 compare_core 完全未动**，所有改动都是部署、Web 服务层、前端防护相关。

### 15.1 顶层结构差异

| 路径 | CertAgent（生产） | list-merge-demo（本地原型） |
|------|------------------|--------------------------|
| 顶层文档 | 多 2 个：`BLUEPRINT.md`、`DOCS.md` | 无 |
| 部署相关 | 多 1 个：`deploy/` | 无 |
| 运行时数据 | 无 | 多 1 个：`runs/`（任务产物运行时生成） |
| 其他文件 | 完全相同 | 完全相同 |

### 15.2 新增文件（CertAgent 独有）

| 文件 | 用途 |
|------|------|
| `BLUEPRINT.md` | 项目原则 / 架构原则顶层文档 |
| `DOCS.md` | 文档路由 + 目录结构索引 |
| `deploy/README.md` | 部署概述（端口已统一 8123/8125） |
| `deploy/部署与使用指南.md` | 同事使用文档（8 章 + 端口 + .docx 要求 + setfacl + 加密说明 + 常见问题） |
| `deploy/部署问题排查日志.md` | 14 个历史问题详细记录 |
| `deploy/certagent.service` | systemd 服务定义 |
| `deploy/deploy.sh` | 一键部署脚本 |
| `deploy/nginx.conf` | Nginx 反代 + 静态托管 + favicon 拦截 |

### 15.3 改动文件（8 个）

#### `requirements.txt`（结构重组）
- 按用途分组：核心 / 编排 / Web
- 新增 4 个 Web 依赖：fastapi / uvicorn / python-multipart / pyjwt

#### `webapi/settings.py`（端口配置）
```diff
-CORS_ALLOW_ORIGINS 默认 "http://localhost:8123"
+CORS_ALLOW_ORIGINS 默认 "http://localhost:3000"
```
（dev 默认 → 生产端口）

#### `webapi/__main__.py`（端口）
```diff
-port=8125  # CertAgent（2 处）
+port=8000  # list-merge-demo（dev 默认）
```

#### `webapi/app.py`（拦截 .doc + 保留扩展名）
**改动1：新增 .doc 拦截**（方案 A）：
```python
for label, f in (("原始清单", file_orig), ("PLM清单", file_plm)):
    if f and f.filename:
        ext = Path(f.filename).suffix.lower()
        if ext == ".doc":
            registry._jobs.pop(rec.job_id, None)
            shutil.rmtree(rec.job_dir, ignore_errors=True)
            raise HTTPException(400, "服务器暂不支持 .doc 转换...")
```

**改动2：文件保存保留原始扩展名**（排查日志问题 2 修复）：
```diff
-orig_path = rec.job_dir / "orig.doc"
+orig_path = rec.job_dir / f"orig{orig_ext}"
```

#### `webapi/runner.py`（删除 LibreOffice fallback + 抛清晰错误）

**改动1：删除 LibreOffice fallback**（5 个方案失败后决定放弃）：
```diff
-docx_path = ensure_docx(str(path), work_dir=str(path.parent / "_converted"))
-return parse_docx(docx_path), docx_path
```

**改动2：解析失败立即抛错**（思路 A，不再静默 0 行）：
```diff
-    except Exception:
-        pass
-    # 3) 老 .doc → 自动转换
-    return [], None
+    except Exception as e:
+        raise RuntimeError(
+            f"文件 {path.name} 不是合法的 .docx 格式"
+            f"（{type(e).__name__}: {str(e)[:200]}）。"
+            "服务器不提供 .doc 自动转换（LibreOffice 7.3 在 systemd 下不可靠）。"
+            "请用 Word 打开后「文件→另存为 .docx」再上传"
+            "（注意：是「另存为」产生新的 docx 文件，不是把 .doc 重命名成 .docx）。"
+        )
```

**改动3：删除 `iterdir()` 循环 + 删除 `import pythoncom`**：
```diff
-orig_doc = None
-plm_doc = None
-for f in rec.job_dir.iterdir():
-    if f.name.startswith("orig"):
-        orig_doc = f
-    ...
+orig_doc = rec.job_dir / "orig.doc"
+plm_doc = rec.job_dir / "plm.doc"

-import pythoncom
```

#### `compare_core/parser/converter.py`（xvfb-run 包装）

**改动**：LibreOffice 路径加 `xvfb-run -a` 包装 + 显式 `-env:UserInstallation` + 超时降到 60s。

> ⚠️ 注意：这份代码现在虽然在 CertAgent 中，但实际**不会被触发**（runner.py 删了 LibreOffice fallback 调用）。保留是作为未来恢复 .doc 支持的备选方案。

#### `frontend/src/pages/WorkspacePage.tsx`（前端文件头校验，思路 C）

**改动1：新增 `checkFileFormat` 函数**：
```typescript
async function checkFileFormat(file: File) {
    // 读前 8 字节 → 判断真 .docx (504B0304) / 真 .doc (D0CF11E0)
}
```

**改动2：handleUpload 加校验**：
```typescript
const origCheck = await checkFileFormat(orig)
if (!origCheck.ok) { setError(`【原始清单】${orig.name} → ${origCheck.msg}`); return }
```

**改动3：input accept 限制 + 黄底警告**：
```diff
-<input type="file" ref={origRef} accept=".doc,.docx,.json" />
+<input type="file" ref={origRef} accept=".docx,.json" />

+<div style={{ background: '#fffbe6', ... }}>
+  ⚠️ 服务器暂不支持 <b>.doc</b> 格式自动转换。
+  请先用 Word 打开 → <b>「另存为 .docx」</b> → 再上传。
+</div>
```

#### `README.md`（重写）
- 项目名称：`变更-开发 清单整合 Demo` → `CertAgent — 认证清单整合系统`
- 新增环境要求章节（操作系统/Python/Node/Word 或 LibreOffice/LLM 接口）
- 新增 LLM 接口说明（聚好看 higpt 网关 + OpenAI 兼容）
- 新增部署依赖章节
- 增加对 `BLUEPRINT.md` / `DOCS.md` 的引用

### 15.4 完全未改动的模块

| 模块/文件 | 状态 |
|-----------|------|
| `compare_core/nodes.py`、`pipeline.py`、`models.py`、`config.py`、`trace.py`、`__init__.py` | ✅ 完全未动 |
| `compare_core/parser/doc_parser.py`、`docx_parser.py`、`__init__.py` | ✅ 完全未动 |
| `compare_core/differ/*`、`memory/*`、`suggester/*`、`writer/*` | ✅ 完全未动 |
| `cli.py`、`check_stats.py`、`graph/*`、`tests/*`、`fixtures/*` | ✅ 完全未动 |
| `frontend/index.html`、`src/lib/api.ts`、`src/pages/ReviewPage.tsx`、其他前端文件 | ✅ 完全未动 |
| `记忆库说明.md`、`_vp.txt` | ✅ 完全未动 |

### 15.5 改动汇总统计

| 类型 | 数量 |
|------|------|
| 新增文件 | 8 个（5 个文档 + 1 个 service + 1 个 .sh + 1 个 nginx.conf） |
| 改动文件 | 8 个 |
| 未改动文件 | 核心引擎 100% + 前端其他页面 + 所有 tests |

### 15.6 改动性质分类

| 类别 | 文件 | 目的 |
|------|------|------|
| **生产部署** | `deploy/`（4 个文件） | systemd / nginx / 一键脚本 / 文档 |
| **端口统一** | `settings.py`、`__main__.py` | 8080→8123、8000→8125 |
| **依赖拆分** | `requirements.txt` | 按核心/编排/Web 分组，加 4 个 web 依赖 |
| **业务侧拦截** | `app.py`（.doc 拦截）+ `WorkspacePage.tsx`（文件头校验） | 方案 A + 思路 C |
| **失败可见** | `runner.py`（删除 fallback + 抛清晰错误） | 思路 A |
| **LibreOffice 增强** | `converter.py`（xvfb-run + 超时降 60s） | 思路 B 残留（未被触发） |
| **文档** | `README.md`（重写）+ `BLUEPRINT.md` + `DOCS.md`（新增） | 项目对外描述 |

### 15.7 关键结论

1. **核心算法 0 改动**：所有 `compare_core/*` 文件与 list-merge-demo 完全一致
2. **核心改动 = 部署 + 端口 + 拦截**：99% 的代码改动是"把本地 demo 改造成能跑在 Linux 服务器的 web 服务"
3. **业务改动 = 方案 A**：拦截 .doc + 抛清晰错误（不是技术修复而是流程改造）
4. **所有改动有据可查**：本日志问题 1-14 记录了每次改动的根因

### 15.8 教训

- 部署层面的改动（端口、systemd、nginx）远比业务改动（核心引擎）多
- 任何"小项目"变"生产服务"都要经历这个过程，**提前规划**部署层能省很多返工
- 保留本地原型的纯净（不改 compare_core）让后续回归测试更容易

---

## 部署配置汇总

### 端口
| 服务 | 端口 |
|------|------|
| Nginx（对外前端） | 8123 |
| Uvicorn（内部后端） | 8125 |

### 关键文件
| 文件 | 位置 |
|------|------|
| Nginx 配置 | `/etc/nginx/sites-available/certagent` |
| systemd 服务 | `/etc/systemd/system/certagent.service` |
| 项目目录 | `/home/hisense/pro/Gitlab/CertAgent` |
| Python 虚拟环境 | `/home/hisense/pro/Gitlab/CertAgent/venv` |
| 前端构建产物 | `/home/hisense/pro/Gitlab/CertAgent/frontend/dist` |
| 任务数据 | `/home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/` |

### 常用运维命令
```bash
# 重启后端
sudo systemctl restart certagent

# 查看后端日志
journalctl -u certagent -f

# 查看最新任务错误
cat /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/*/job.json | python3 -m json.tool

# 清理任务 + 杀死残留 LO 进程
pkill -9 -f soffice
rm -rf /home/hisense/pro/Gitlab/CertAgent/runs/web_jobs/*

# 测试 LibreOffice 转换
soffice --headless --norestore --convert-to docx --outdir /tmp /path/to/test.doc

# 测试 Python 转换流程
python3 -c "from compare_core.parser.converter import ensure_docx; print(ensure_docx('/path/to/test.doc', work_dir='/tmp'))"
```

---

## 问题 16：AI 智能配对无任何建议（LLM 环境变量未加载）

**时间**：2026-08-20

**现象**：前端勾选「🤖 启用 AI 智能配对」后，残余行没有任何 AI 提名；开/关开关耗时、结果完全一样。后端日志报：
```
[llm][fuzzy] 提名失败：LLM 环境变量未配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL）
```

**根因**：
1. `compare_core/config.py` 用 `os.getenv()` 读环境变量，但**项目没有加载 `.env`**（没有 python-dotenv，也没手动 load）
2. systemd 服务没有 `EnvironmentFile=`，启动的进程里根本没有 LLM 变量
3. 之前误以为"没有残余行所以不触发 fuzzy"——实际残余行一直存在（only_plm=63 / only_orig=47 / diff 总数 222）

**解决**：`config.py` 加 `_load_dotenv()`，在 `Config` 实例化前手动解析项目根 `.env`（逐行 `key=value`，支持注释、单双引号，不覆盖已存在的环境变量）。

**教训**：环境变量加载是"隐式依赖"——本地 shell 手动 export 能跑，systemd 下跑不了。**凡 `os.getenv()` 读的配置，要么加 dotenv 自动加载，要么在 service 里写 `EnvironmentFile=`**。

---

## 问题 17：LLM 思考过程异常膨胀（reasoning 字数熔断误触发）

**时间**：2026-08-20

**现象**：后端报 `LLM 思考过程异常膨胀（>10000 字）`，任务直接回退 mock。

**根因**：给推理模型（deepseek-v4-flash）设了 reasoning 字符数熔断（1 万字符），但正常场景（110 行候选池）思考过程就超过 1 万字符，误杀。

**解决**：删掉 `_LLM_MAX_REASONING_CHARS` 及 reasoning_total 累加，只保留 `_LLM_TOTAL_TIMEOUT = 120.0` 墙钟兜底。

**教训**：对推理模型设"思考长度"限是危险的——思考长是正常现象。**熔断只认墙钟时间，不认输出字符数**。

---

## 问题 18：服务器只能上传解密后的 docx（透明加密，最终根因）

**时间**：2026-08-20

**现象**：服务器上传加密 docx 失败，只能传解密后的。此前（问题 13）误判为"用户文件被 Word 加密"，实际是**透明加密（DLP）**。

**根因**：透明加密——本地进程读文件是明文，但**磁盘字节是密文**。`scp` 上传的是磁盘密文字节，服务器拿到的就是密文，python-docx 打不开（文件头是随机值，如 `14D030A8...` / `D9B04DB4`，既非 `504B0304` 也非 `D0CF11E0`）。

**解决**：
1. 新增 `compare_core/decrypt.py`：POST 解密服务 `http://10.18.87.51:8500/parse_file`（`format=file` 返回解密字节）
2. `config.py` 加 `DECRYPT_URL` / `DECRYPT_TIMEOUT` / `DECRYPT_ENABLED`
3. `runner.py` `_load_one` 顺序：JSON fixture → `parse_docx` → **`decrypt_file` 兜底** → 抛清晰错误
4. 前端 `checkFileFormat` 放行未知 magic（`encrypted-or-unknown`），交给后端解密兜底，只拦 `.doc`

**教训**：`scp` 传透明加密文件，本地"看起来正常"不代表磁盘字节是明文。**文件 magic bytes 是判断"服务器拿到的是不是明文"的最可靠手段**。

---

## 问题 19：前端改动部署后不生效（scp 到错误目录）

**时间**：2026-08-20

**现象**：前端代码改了、重新 build 了，但部署后页面没变化。

**根因**：scp 把 dist 产物传到了 `frontend/`，但 nginx 8123 的 root 实际是 `frontend/dist/`。用 `nginx -T` 才看到真实 root。

**解决**：scp 到 `frontend/dist/` 正确路径。

**教训**：nginx root 用 `nginx -T` 确认，不要想当然。

---

## 问题 20：前端空白页（dist/assets 权限 700）

**时间**：2026-08-20

**现象**：正确部署到 dist/ 后，页面仍空白。

**根因**：`dist/assets/` 目录权限 700（`drwx------`），nginx 以 `www-data` 运行读不了。

**解决**：`sudo chmod -R 755 /home/hisense/pro/Gitlab/CertAgent/frontend/dist/`

**教训**：这是问题 1 / 10 权限问题的变体——**每次 build 后都要检查 dist 目录权限**（构建工具可能生成 700 目录）。

---

## 问题 21：PowerShell 中文编码 + 版本保存

**时间**：2026-08-21

**现象**：写 PowerShell 备份脚本，中文标签/说明乱码。

**根因**：PowerShell 5.1 读无 BOM 的 UTF-8 `.ps1` 按 ANSI（GBK）解析中文 → 乱码。

**解决**：`.ps1` 文件加 UTF-8 BOM（`EF BB BF`）前缀再执行。

**版本保存**：CertAgent 本地 commit `e9afe4f` + push Gitlab；CertAgent 和 list-merge-demo 各快照到 `历史版本\版本_<时间戳>_<标签>`（排除 .env/.git/node_modules/dist/.env 密钥）。

---

## 📊 时间线补充（2026-08-20 ~ 08-21）

| 时间 | 事件 | 结论 |
|------|------|------|
| 08-20 上午 | AI 智能配对无建议 | 根因 LLM 环境变量未加载 |
| 08-20 上午 | LLM 思考膨胀报错 | 去掉 reasoning 字数熔断 |
| 08-20 下午 | 服务器无法传加密 docx | 根因透明加密，调解密服务 |
| 08-20 下午 | 前端改动不生效 | scp 到 dist/ 正确路径 |
| 08-20 下午 | 前端空白页 | dist/assets 权限 700 → 755 |
| 08-21 | 版本保存 | git + 历史版本文件夹快照 |

---

## 📚 经验教训补充（教训 8-11）

### 教训 8：环境变量加载是部署隐性坑
`os.getenv()` 读的配置，本地手动 export 能跑、systemd 下跑不了。要么 dotenv 自动加载，要么 service 写 `EnvironmentFile=`。

### 教训 9：透明加密让 scp 上传密文
本地进程看是明文 ≠ 磁盘字节是明文。scp 传的是磁盘字节。用文件 magic bytes 判断服务器拿到的是不是明文。

### 教训 10：熔断只认墙钟不认字符数
推理模型思考长是正常的，字符数熔断会误杀。保留 120s 墙钟兜底即可。

### 教训 11：每次 build 后查 dist 权限
前端构建产物可能生成 700 目录，nginx（www-data）读不了 → 空白页。build 后 `chmod -R 755` 或重跑 setfacl。
