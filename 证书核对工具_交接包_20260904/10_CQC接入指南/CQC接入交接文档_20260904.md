# CQC 接入交接文档 · 两份工作合并指南

> **⚠️ 重要前提**：CQC 的工作**分在两个人手里**——
> - **A（本机/本文档维护者）**：已完成 CQC 的 **OAuth2 框架层**（授权流程 + 令牌管理 + 网页端点 + 网关调用封装），但**没有证书查询 provider**。
> - **B（另一位交接人）**：持有 **CQC 证书查询的业务代码**（providers/cqc.py 及相关），在其自己的环境开发，**后续合并到本项目**。
>
> 本文档的定位是：**告诉合并者，A 这边有什么、缺什么、B 要补什么、两边怎么对接、合并时要注意什么**。

---

## 目录

1. [现状：两边各有什么](#1-现状两边各有什么)
2. [A 这边已完成的代码（OAuth2 框架层）](#2-a-这边已完成的代码oauth2-框架层)
3. [B 需要提供的代码（证书查询层）](#3-b-需要提供的代码证书查询层)
4. [合并对接点：接口契约](#4-合并对接点接口契约)
5. [合并操作步骤](#5-合并操作步骤)
6. [合并后必须验证的检查清单](#6-合并后必须验证的检查清单)
7. [存在的误区与陷阱](#7-存在的误区与陷阱)
8. [如果 B 的代码与 A 的框架冲突怎么办](#8-如果-b-的代码与-a-的框架冲突怎么办)

---

## 1. 现状：两边各有什么

```
┌─────────────────────────────────────────────────────────┐
│                    CQC 接入全貌                           │
│                                                          │
│  ┌──────────────── A（本机）已完成 ─────────────────┐    │
│  │  OAuth2 授权码流程                               │    │
│  │  令牌持久化 + 自动续期                            │    │
│  │  网页端点 (status/authorize/callback)             │    │
│  │  网关调用封装 call_gateway()                      │    │
│  │  授权登录脚本 cqc_login.py                        │    │
│  │  Java 参考实现 (client-demo-origin)               │    │
│  └──────────────────────────────────────────────────┘    │
│                         │                                │
│                    接口：call_gateway()                   │
│                    返回：JSON 文本                         │
│                         │                                │
│  ┌──────────────── B（另一人）待合并 ───────────────┐    │
│  │  providers/cqc.py  ← 证书查询 Provider            │    │
│  │  · 调 call_gateway() 拿 CQC 证书数据              │    │
│  │  · 解析 JSON → 映射到 CertRecord                   │    │
│  │  · CQC 证书号识别/归一化                           │    │
│  │  路由注册 (providers/__init__.py 改动)             │    │
│  │  前端关键词 (app.js FIELD_KEYWORDS 改动)            │    │
│  │  可能还有：离线测试、fixtures                       │    │
│  └──────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 一句话总结

**A 做完了"怎么连上 CQC"（认证+通道），B 做的是"连上之后查什么、怎么解析"（业务）。** 合并就是把 B 的 providers/cqc.py 放进来，接到 A 的 `call_gateway()` 上。

---

## 2. A 这边已完成的代码（OAuth2 框架层）

以下代码**已在 `crawl4ai-main/cert_verify/` 中存在**，合并时**不要覆盖**（除非 B 有明确的改进理由）。

### 2.1 `cqc_oauth.py`（262行）— OAuth2 核心

| 函数 | 职责 | B 会用到吗 |
|---|---|---|
| `is_configured()` | 检查 client_id/secret/redirect_uri 是否齐全 | 可能用 |
| `is_authorized()` | 检查本地是否有 refresh_token | **会用** |
| `status()` | 返回授权状态字典 | 不直接用 |
| `new_state()` / `check_state(state)` | state 生成与校验（防 CSRF） | 不用 |
| `exchange_code(code)` | 用授权码换 token | 不用（框架内部） |
| `refresh()` | 用 refresh_token 换新 access_token | 不用（框架内部） |
| `get_access_token(margin=60)` | 返回有效 access_token，快过期自动续期 | 不用（被 call_gateway 封装） |
| **`call_gateway(method, version, extra_params)`** | **网关调用：自动带 Bearer token，返回 JSON 文本** | **★ B 的核心依赖** |
| `clear()` | 清除本地令牌 | 不用 |

> **B 最关键的接口就是 `call_gateway()`**——它封装了"取 token → 自动续期 → 带 Bearer → 调网关 → 返回文本"的全过程。B 的 provider 只需调它拿到 JSON 再解析。

### 2.2 `cqc_login.py`（154行）— 授权登录脚本

弹浏览器人工授权，存 refresh_token。**合并后这文件不用动**。

### 2.3 `web/server.py` 的 3 个 CQC 端点（已集成）

| 端点 | 方法 | 功能 | 合并时 |
|---|---|---|---|
| `/api/cqc/status` | GET | 返回授权状态 | 不动 |
| `/api/cqc/authorize` | GET | 发起授权→302 跳 CQC | 不动 |
| `/api/cqc/callback` | GET | 回调换 token | 不动 |

> **注意**：这 3 个端点只做授权流程，不做证书查询。证书查询走 `ProviderRouter` → `CqcProvider.query()`，与网页的核对任务流程一致（和 VDE/UL/TÜV 一样）。

### 2.4 令牌存储

| 路径 | 内容 | 合并时 |
|---|---|---|
| `.cqc_profile/token.json` | access_token + refresh_token | 不提交 Git、不打包 |
| `.cqc_profile/browser/` | cqc_login 的浏览器配置 | 不提交 Git、不打包 |

### 2.5 Java 参考实现

`client-demo/client-demo-origin/` 是 CQC 官方提供的 Spring Boot 示例。Python 版的 `cqc_oauth.py` 是参照它写的，流程一致。**合并 Java 版不参与**（它是参考，不是运行代码）。

---

## 3. B 需要提供的代码（证书查询层）

以下是**预期 B 会带来的文件**。合并者需要从 B 那里拿到这些并放入对应位置：

| B 的文件 | 放到哪 | 职责 |
|---|---|---|
| `cqc.py`（或类似） | `cert_verify/providers/cqc.py` | ★ 核心：CqcProvider 实现 |
| `__init__.py` 的改动 | 合并进 `cert_verify/providers/__init__.py` | ProviderRouter 注册 + 路由 |
| `app.js` 的改动 | 合并进 `cert_verify/web/static/app.js` | FIELD_KEYWORDS 加 CQC |
| 测试（如有） | `cert_verify/tests/test_cqc_*.py` | 离线解析测试 |
| fixtures（如有） | `cert_verify/tests/fixtures/` | 真实返回 JSON 样本 |

### B 的 `providers/cqc.py` 应该长什么样

基于 A 已提供的框架，B 的 provider 结构应该是：

```python
from .. import cqc_oauth          # ← 用 A 的框架
from ..models import CertRecord, FOUND, NOT_FOUND, ERROR
from .base import BaseProvider

class CqcProvider(BaseProvider):
    name = "CQC"
    number_patterns = (...)       # ← B 定义：CQC 证书号识别

    def query(self, cert_no: str) -> CertRecord:
        rec = CertRecord(query_no=cert_no, source=self.name)

        # 1. 确认已授权（用 A 的函数）
        if not cqc_oauth.is_authorized():
            rec.status = ERROR
            rec.message = "CQC 未授权"
            return rec

        # 2. 调网关（用 A 的函数）—— method/version/参数是 B 定义的
        raw = cqc_oauth.call_gateway(
            method="<B 确认的证书查询 method>",
            version="<B 确认的 version>",
            extra_params={"<证书号参数名>": self._normalize(cert_no)},
        )

        # 3. 解析 JSON → 映射到 CertRecord —— B 的核心工作
        data = json.loads(raw)
        # ... B 的解析逻辑 ...
        return rec
```

---

## 4. 合并对接点：接口契约

A 和 B 之间的**唯一接口契约**是 `cqc_oauth.call_gateway()`：

```python
def call_gateway(method: str, version: str, extra_params: dict | None = None) -> str:
    """按官方示例形式调用 open-gateway，自动带 Bearer token，返回响应文本。

    URL 形如：{gateway}?client_id=..&method=..&version=..&timestamp=..&<extra>
    具体 method/version/参数以平台「我的接口」页接口定义为准。
    """
```

| 对接项 | A 提供 | B 依赖 |
|---|---|---|
| 函数名 | `cqc_oauth.call_gateway` | `from .. import cqc_oauth; cqc_oauth.call_gateway(...)` |
| 入参 1 | `method: str` | B 填平台定义的 method 名 |
| 入参 2 | `version: str` | B 填版本号（如 `"1.0"`） |
| 入参 3 | `extra_params: dict \| None` | B 填业务参数（如证书号、日期、页码） |
| 返回值 | `str`（响应文本） | B 自己 `json.loads()` 解析 |
| 异常 | `RuntimeError`（网络/认证失败） | B 需要 try/except |
| 认证 | A 自动处理（Bearer token + 自动续期） | B **不用管** |
| 代理 | A 自动处理（默认直连） | B **不用管** |

> **合并验证的第一步**：确认 B 的代码调 `cqc_oauth.call_gateway()` 的方式与 A 的签名一致。如果 B 用了不同的函数名或签名，需要适配。

---

## 5. 合并操作步骤

### 第一步：拿到 B 的代码

从 B 那里获取以下文件（清单见第 3 节）。确认：
- [ ] `providers/cqc.py`（或 B 叫的其他名字）
- [ ] `providers/__init__.py` 的改动部分（路由注册）
- [ ] `web/static/app.js` 的改动部分（FIELD_KEYWORDS）
- [ ] 测试文件和 fixtures（如有）
- [ ] B 确认了 CQC 证书查询的 method/version/参数名（从平台"我的接口"页）

### 第二步：备份 A 的当前状态

```powershell
# 备份整个项目
cd "C:\Users\likunyuan\Desktop\爬取项目"
# 建议先 git commit 或复制 crawl4ai-main 到 crawl4ai-main_backup
```

### 第三步：放入 B 的文件

```powershell
$root = "C:\Users\likunyuan\Desktop\爬取项目\crawl4ai-main"

# 1. 放 providers/cqc.py
# 把 B 的文件复制到 $root\cert_verify\providers\cqc.py

# 2. 放测试（如有）
# 把 B 的测试复制到 $root\cert_verify\tests\
```

### 第四步：合并 `providers/__init__.py`

这是**最容易出冲突**的地方，因为 A 和 B 都改这个文件。需要手动合并：

```python
# A 的当前状态（providers/__init__.py 已有的）：
from .tuv_rheinland import TuvRheinlandProvider
from .ul import UlProvider
from .vde import VdeProvider

# B 需要加的：
from .cqc import CqcProvider    # ← B 加这行

class ProviderRouter:
    def __init__(self, ...):
        ...
        self.providers: dict[str, BaseProvider] = {
            "VDE": VdeProvider(...),
            "UL": ul,
            "TUV-Rheinland": TuvRheinlandProvider(client),
            # B 加：
            "CQC": CqcProvider(),     # ← B 加这行
        }

    _PREFIX_RE = re.compile(
        r"^\s*(UL|VDE|T[ÜU]V|ENEC|CQC)\b", re.I)  # ← B 在正则加 CQC

    def resolve(self, cert_no, agency_hint=""):
        # B 加：
        pref = self._cert_prefix(cert_no)
        if pref == "CQC":
            return self.providers["CQC"]         # ← B 加
        # ... 保留 A 的原有逻辑
        h = (agency_hint or "").strip().lower()
        if "cqc" in h:                           # ← B 加
            return self.providers["CQC"]         # ← B 加
```

> **合并原则**：不要用 B 的文件直接覆盖 A 的 `__init__.py`，而是**把 B 的改动手动追加**到 A 的文件中。A 的文件里有 VDE/UL/TÜV 的路由逻辑，直接覆盖会丢。

### 第五步：合并 `web/static/app.js`

同样手动合并 `FIELD_KEYWORDS`：

```javascript
// A 的当前状态（app.js 已有的）：
const FIELD_KEYWORDS = {
  cert_no: [...],
  agency: ["认证类别", "认证类型", "认证机构", "机构", "类别", "agency"],
  // B 在 agency 的关键词列表里加 "CQC"：
  // agency: ["认证类别", ..., "CQC", "中国质量认证", "cqc"],
  ...
};
```

### 第六步：确认环境变量

确保 `.env` 或环境变量中配置了 CQC：

```ini
CQC_ENABLED=true
CQC_CLIENT_ID=<平台分配>
CQC_CLIENT_SECRET=<开发者设置>
CQC_REDIRECT_URI=<回调地址，与平台一致>
```

### 第七步：跑离线测试确认不破坏现有功能

```powershell
$PY = "C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe"
cd "C:\Users\likunyuan\Desktop\爬取项目\crawl4ai-main"

# A 的 5 套离线测试必须仍然全绿（确认合并没有破坏 VDE/UL/TÜV）
& $PY cert_verify\tests\test_routing.py
& $PY cert_verify\tests\test_vde_parse.py
& $PY cert_verify\tests\test_compare_fields.py
& $PY cert_verify\tests\test_excel_writer.py
& $PY cert_verify\tests\test_web_flow.py

# 如果 B 带了测试，也跑
& $PY cert_verify\tests\test_cqc_parse.py    # 如有
```

### 第八步：端到端验证

```powershell
# 完成授权
& $PY -m cert_verify.cqc_login http://127.0.0.1:8756

# 单条查询
& $PY -m cert_verify.cli query <CQC证书号> --agency CQC

# 网页版上传含 CQC 证书的 Excel 核对
& $PY -m cert_verify.web --port 8756
```

---

## 6. 合并后必须验证的检查清单

| 检查项 | 命令 / 方法 | 期望结果 |
|---|---|---|
| VDE 路由未坏 | `test_routing.py` | 全部通过 |
| VDE 解析未坏 | `test_vde_parse.py` | 全部通过 |
| 字段比对未坏 | `test_compare_fields.py` | 全部通过 |
| Excel 写入未坏 | `test_excel_writer.py` | 全部通过 |
| 网页全链路未坏 | `test_web_flow.py` | 全部通过 |
| CQC provider 可导入 | `py -3.12 -c "from cert_verify.providers import CqcProvider; print('ok')"` | ok |
| 路由器含 CQC | `py -3.12 -c "from cert_verify.providers import ProviderRouter; r=ProviderRouter(); print('CQC' in r.providers)"` | True |
| CQC 证书号能路由到 CQC | `py -3.12 -c "from cert_verify.providers import ProviderRouter; r=ProviderRouter(); print(r.resolve('CQC 12345','CQC').name)"` | CQC |
| 已有证书号不被误路由到 CQC | `py -3.12 -c "from cert_verify.providers import ProviderRouter; r=ProviderRouter(); print(r.resolve('40052271','VDE').name)"` | VDE（不是 CQC） |
| 授权状态 | `curl http://127.0.0.1:8756/api/cqc/status` | authorized: true（授权后） |
| 网关可调 | `cqc_oauth.call_gateway(...)` | 返回 JSON |
| 网页能核对 CQC | 上传含 CQC 证书的 Excel | 结果正确 |

---

## 7. 存在的误区与陷阱

### 误区 1：直接用 B 的文件覆盖 A 的 `__init__.py`

**误区**：B 在自己环境开发时改了 `providers/__init__.py`，合并时直接用 B 的覆盖 A 的。

**后果**：A 的 `__init__.py` 里有 VDE/UL/TÜV 的完整路由逻辑（前缀优先、认证类别路由、形态猜测、close() 释放 UL 会话等），直接覆盖会**把这些全丢掉**，导致 VDE/UL/TÜV 全部不能路由。

**对策**：手动合并——把 B 的 CQC 相关改动**追加**到 A 的文件中，保留 A 的所有原有逻辑。

### 误区 2：B 的代码里自带了一份 `cqc_oauth.py`

**误区**：B 为了独立开发，可能自己也写了一份 OAuth2 框架（或修改了 A 的），合并时两份冲突。

**对策**：**以 A 的 `cqc_oauth.py` 为准**。A 的框架已经在本机经过验证。如果 B 的 provider 依赖 A 没有的函数，让 B 指出缺哪个，在 A 的 `cqc_oauth.py` 里补，而不是换成 B 的版本。

### 误区 3：CQC 证书号与 VDE/TÜV 冲突导致误路由

**误区**：CQC 证书号如果是纯数字（如 `12345678`），会被 A 的路由规则 `^\d{8}$` 匹配到 TÜV，或被 `^\d{7,8}$` 匹配到 VDE。

**后果**：CQC 证书被送到 TÜV/VDE 查询，查不到或查错。

**对策**：
- 优先靠表格"认证类别"列路由（含 `CQC` → CQC）—— 这条要在 B 的路由改动里加上
- 如果 CQC 证书号有固定前缀（如 `CQC xxx`），靠前缀路由
- 形态猜测（最后一级）要确保 CQC 的 `number_patterns` 不会和 VDE/TÜV 重叠，或 CQC 排在前面

### 误区 4：以为合并完就能查证书

**误区**：代码合并完、provider 放进去了，就能查 CQC 证书了。

**真相**：代码合并只是第一步。还需要：
1. 在 CQC 平台注册应用拿到 client_id/secret（外部操作）
2. 配置 redirect_uri（平台和本地必须一致）
3. 完成 OAuth2 授权（`cqc_login`，一次性）
4. 确认平台已开通证书查询接口（method/version）
5. 填 `.env` 环境变量

**对策**：代码合并后按第 5 节的完整步骤走，不要跳。

### 误区 5：CQC 走境外代理

**误区**：把 CQC 也配成走 `127.0.0.1:7877`（VDE/UL/TÜV 用的境外代理）。

**真相**：CQC 是**境内平台**（`open.cqccms.com.cn`），**直连**即可。走境外代理反而连不上。

**对策**：`CQC_PROXY` 保持空（默认直连）。

### 误区 6：redirect_uri 不一致

**误区**：本地开发填 `127.0.0.1:8756`，服务器改了但平台没同步改。

**真相**：redirect_uri 必须与平台配置**精确一致**（含路径），否则报 `redirect_uri mismatch`。

**对策**：切换环境时同步改平台配置。或开发期在平台配两个回调地址。

### 误区 7：refresh_token 永久有效

**误区**：授权一次后再也不用管。

**真相**：refresh_token 可能因平台撤销、长期不用、改配置而失效。

**对策**：代码已实现自动续期，但 refresh 本身失败时要重新授权。`cqc_login` 重新跑一次即可。

### 误区 8：以为 dashboard.html 里的 4 个 API 就是证书查询

**误区**：直接拿 `cmslab.labtask.listAppTest` 等当证书查询用。

**真相**：这 4 个是**实验室任务查询**（按日期分页），**不是按证书号查证书**。证书核对需要的是"输入证书号→返回证书信息"的接口。

**对策**：这 4 个 API 的价值是展示**网关调用格式**。真正的证书查询接口需 B 在平台"我的接口"页确认。

### 误区 9：合并后没跑 A 的原有测试

**误区**：合并完直接验证 CQC，没跑 A 的 5 套离线测试。

**后果**：B 的改动可能破坏了 VDE/UL/TÜV 的路由或比对，但你不知道。

**对策**：合并后**第一件事**跑 A 的 5 套测试，全绿才继续。

### 误区 10：令牌文件进了 Git

**误区**：`.cqc_profile/token.json` 含密钥，不小心提交了。

**对策**：确保 `.gitignore` 含 `.cqc_profile/`。`build_package.py` 不打包此目录。

---

## 8. 如果 B 的代码与 A 的框架冲突怎么办

### 情况 1：B 用的 `call_gateway` 签名与 A 不同

**B 可能写了**：`call_gateway(method, version, cert_no)`（把证书号直接传）
**A 的签名**：`call_gateway(method, version, extra_params: dict)`

**对策**：在 B 的 provider 里适配，把证书号包进 dict：
```python
# B 改成：
raw = cqc_oauth.call_gateway(
    method="...",
    version="1.0",
    extra_params={"certificateNo": cert_no},   # ← 包进 dict
)
```

### 情况 2：B 的 provider 不用 `cqc_oauth` 而是自己管 token

**B 可能写了**：自己的 HTTP 请求 + 自己的 token 管理。

**对策**：**优先让 B 改用 A 的 `cqc_oauth.call_gateway()`**。A 的框架已处理了自动续期、直连代理、错误处理。如果 B 有充分理由不用（如 A 的框架缺功能），在 A 的 `cqc_oauth.py` 里补功能，不要让 B 另起一套。

### 情况 3：B 改了 `models.py` 的 CertRecord

**B 可能加了**：CQC 特有的字段。

**对策**：CertRecord 是**全机构统一模型**，加字段要谨慎。如果 CQC 确实有其他机构没有的字段，加到 `raw_fields` dict 里，不要加新的顶级属性（会影响比对层和 Excel 写入层）。

### 情况 4：B 的路由优先级与 A 冲突

**B 可能写了**：CQC 排在 VDE 前面，导致纯数字证书号全去 CQC。

**对策**：路由优先级必须是：**显式前缀 > 认证类别列 > 形态猜测**。CQC 只在"认证类别列含 CQC"或"证书号有 CQC 前缀"时才路由到 CQC。形态猜测层不要让 CQC 抢在 VDE/TÜV 前面。

### 情况 5：B 的代码编码不是 UTF-8

**B 可能用了**：`Set-Content` 或 GBK 编辑器，导致中文注释乱码。

**对策**：合并后检查 B 的文件编码。用编辑器另存为 UTF-8。PowerShell 的 `Set-Content` 会破坏 UTF-8 中文——改代码用编辑器或 `edit` 工具，别用 `Set-Content`。

---

## 附：合并交接清单（给 B 的 checklist）

合并前向 B 确认以下信息：

- [ ] B 的 `providers/cqc.py` 文件
- [ ] B 确认的 CQC 证书查询 **method 名**（从平台"我的接口"页）
- [ ] B 确认的 **version**（如 `1.0`）
- [ ] B 确认的**证书号参数名**（如 `certificateNo`）
- [ ] B 确认的 **返回 JSON 结构**（字段名如何映射到制造商/型号/标准/状态）
- [ ] B 确认的 **CQC 证书号格式**（纯数字？带前缀？几位？）
- [ ] B 对 `providers/__init__.py` 的改动（路由注册）
- [ ] B 对 `web/static/app.js` 的改动（FIELD_KEYWORDS）
- [ ] B 的离线测试和 fixtures（如有）
- [ ] B 是否改了 `cqc_oauth.py`（如有，说明改了什么、为什么）
- [ ] B 是否改了 `models.py`（如有，说明加了什么）
- [ ] B 是否在 CQC 平台注册了应用（client_id/secret）
- [ ] B 是否已开通证书查询接口权限
