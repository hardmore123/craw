# LLM 自动适配器生成技术方案

> 版本：v0.1（草案，供评审）
> 目标读者：项目负责人 / 后续实现工程师
> 状态：**方案评审阶段，尚未动任何抓取代码**

---

## 1. 背景与目标

### 1.1 现状

当前每接入一个品牌官网，都要**手写一份 Python 适配器**（`overseas/sites/<code>/adapter.py`），里面写死了该站特有的：

- 入口 URL 与分页方式（LG 用 `firstResult=` 翻页，Samsung 用官方 finder JSON 接口，Hisense 用 View All 页）
- 型号链接匹配正则（如 LG 的 `_PROD_RE`）
- 规格表 DOM 选择器（各家不同）
- 反爬等级、等待锚点、滚动/点击展开策略

一个新站从 0 到可用，通常要写并调试几百行代码。这是当前**人工成本的主要来源**。

### 1.2 目标

把「接一个新站」从**写代码**降级为**给一个 URL + 审一份配置**：

- 程序自动探测网站结构 → LLM 生成结构化配置（AdapterSpec）→ 自动小样试跑 → 自动打分 → 不达标自动修 → 达标存库
- 第二次及以后访问该站：直接读已保存的 Spec 全量抓取，**不再调用 LLM**
- 官网改版导致 Spec 失效时，自动重新触发闭环

### 1.3 非目标（明确不做/做不到）

- **不追求任意网站零人工全自动**。强反爬站、验证码站、闭环不收敛站仍需人工介入或放弃。
- **不让 LLM 直接产出可执行 Python 代码**。只产出受控的 JSON 配置，由固定引擎执行。
- 本方案只覆盖 **SPEC（规格）抓取线**。价格/网评零售线结构差异更大，先不纳入。

---

## 2. 设计原则

| 原则 | 说明 | 解决什么风险 |
|---|---|---|
| LLM 产出**配置**而非代码 | AdapterSpec 是 JSON，引擎能力固定 | 坏 Spec 最多抓不到，不会执行恶意/失控逻辑 |
| **审计表即 reward 函数** | 复用现有 `spec_entry_audit`、`spec_series_status` 打分 | 避免闭环"自我感觉良好"，用真实入库数据判分 |
| **优先官方接口** | 探测先抓 XHR，命中 JSON 接口就走接口 | 比解析 DOM 稳（Samsung finder 就是例证）|
| **小样先行** | 每轮只跑 1~2 个系列判分，达标才全量 | 省 token、省时间、快速失败 |
| **守恒校验** | 入口发现 N 个型号 → 落库必须也是 N 个 | 防 Samsung `family_id` 那类"看着齐了其实少了"的隐蔽 bug |
| **人工兜底可追溯** | 不收敛时保存完整现场 | 让人能快速接手，而非从零排查 |

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     spec-onboard (首次接入)                    │
│                                                               │
│  Probe ──► Generate ──► Run(小样) ──► Verify ──► [达标?]        │
│   探测       LLM 生成      引擎试跑     审计打分      │            │
│    ▲                                              否│           │
│    │                                                ▼           │
│    └──────────────── Repair(回喂失败样本) ◄──── [轮次<N?]        │
│                                                    否│          │
│                                                      ▼          │
│                                          标记"需人工"+保存现场    │
│                                                               │
│  [达标] ──► 人工审一眼 ──► Persist(存 Spec) ──► 全量抓取         │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              spec-crawl (日常重跑，第二次起)                    │
│   读已存 Spec ──► 通用引擎全量抓 ──► 导出                        │
│   （完全不调用 LLM）                                           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              spec-health (定期健康探测)                        │
│   跑已存 Spec 小样 ──► 成功率骤降? ──► 自动触发 onboard 重生成    │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 与现有代码的关系

- **不改**现有手写适配器；新引擎作为一个**新的 `SpecAdapter` 子类**（如 `GenericSpecAdapter`），吃 AdapterSpec 跑通用逻辑，通过现有 `SiteRegistry` 机制注册。
- 复用现有：`fetchers.py`（浏览器/DOM）、`spec_parser.py`（键值解析）、`db.py`（`save_spec_sheet` / `record_spec_series_status` / `record_spec_entry_audit`）、`spec_identity.py`（family 身份）。
- 新增：探测器、Spec 存储表、校验器、闭环控制器、LLM 客户端。

---

## 4. AdapterSpec 数据结构

AdapterSpec 是整个方案的核心契约。LLM 只能产出符合此 schema 的 JSON，引擎只认此 schema 的字段。

```jsonc
{
  "spec_version": "1.0",
  "code": "sony_uk",                    // 品牌/站点代号
  "brand_name": "Sony (UK)",
  "region": "uk",
  "entry_url": "https://www.sony.co.uk/.../televisions",
  "generated_at": "2026-09-15T10:00:00Z",
  "generated_by": "llm:gpt-x / probe-report#123",
  "status": "candidate",                // candidate | approved | deprecated

  // ── 阶段一：发现型号（三选一或组合）──
  "discover": {
    "mode": "xhr_api",                  // xhr_api | dom_anchor | scroll | paginate
    "wait_selector": "a[href*='/tv/']", // 入口页等待锚点

    // mode=xhr_api 时（最优先）
    "api": {
      "url_template": "https://.../finder?type=tv&num=500",
      "method": "GET",
      "list_path": "response.resultData.productList",  // JSON 中列表位置
      "model_field": "modelCode",
      "url_field": "pdpUrl",
      "series_field": "familyName",
      "family_id_field": "familyId"     // 有则用作身份键，防同名覆盖
    },

    // mode=dom_anchor / scroll / paginate 时
    "model_link_selector": "a[href*='/tv/']",
    "model_url_regex": "/tv/([a-z0-9-]+)/?$",
    "series_from": "url_regex | card_text | api_field",

    // 分页策略
    "paginate": {
      "type": "query_param",            // query_param | click_more | scroll | none
      "param": "firstResult",
      "step": 30,
      "max_pages": 50,
      "click_selector": "",             // type=click_more 时
      "growth_selector": "a[href*='/tv/']",  // 判断是否还在增长
      "stable_rounds": 2                // 连续 N 轮不增长即停
    }
  },

  // ── 阶段二：解析单个型号规格页 ──
  "spec": {
    "page_wait": "[class*='spec']",
    "extract_mode": "kv_pairs",         // kv_pairs | table | json_ld
    "table_selector": "[class*='spec'] [class*='item']",
    "key_selector": "[class*='tit'],[class*='name'],dt",
    "value_selector": "[class*='desc'],[class*='value'],dd",
    "json_ld_type": ""                  // extract_mode=json_ld 时
  },

  // ── 身份与去重 ──
  "identity": {
    "series_key": "family_id",          // family_id | series_name | url_slug
    "model_normalize": "upper_alnum"    // 型号归一化规则
  },

  // ── 反爬/礼貌 ──
  "fetch": {
    "protection": "L2_MEDIUM",
    "interval_sec": 5.0,
    "extra_wait_ms": 6000,
    "requires_browser": true
  },

  // ── 期望值（审计参考，不参与硬性准入）──
  "expected": {
    "series_count": null,               // 官网标称系列数（有则填）
    "model_count": null
  }
}
```

**关键点**：`discover.mode` 决定引擎走哪条发现路径；`identity.series_key` 直接对应我们已经踩过坑的 Samsung family 问题；`expected` 是软参考，实际以审计为准。

---

## 5. 六阶段闭环详解

### 5.1 Probe（探测）

**输入**：`code`、`entry_url`
**动作**：浏览器打开入口页，采集：

1. **精简 DOM**：去 `<script>`/`<style>`/内联事件，保留标签结构和 class/href，截断到约 30~50KB
2. **候选链接**：页面所有 `<a href>`，按 URL 模式聚类（哪些像产品页）
3. **XHR/接口清单**：监听网络请求，记录返回 JSON 的接口 URL、请求参数、响应结构摘要
4. **动态行为探测**：滚动一次、点一次"加载更多"，记录链接数量是否增长

**输出**：一份「探测报告 JSON」（不含整页原文，只含结构化线索），存盘留档。

**为什么这样设计**：整页 HTML 喂 LLM 又贵又噪。精简后的结构 + 接口清单信息密度最高，且能命中官方接口这条"捷径"。

### 5.2 Generate（生成）

**输入**：探测报告
**动作**：调 LLM，system prompt 固定要求"只输出符合 AdapterSpec schema 的 JSON"，用 JSON schema 约束 + 解析校验双保险。
**输出**：一份 AdapterSpec（`status=candidate`）。

**护栏**：
- 输出必须通过 JSON schema 校验，不合法直接判该轮失败
- 选择器/正则做静态合法性检查（能编译、不含明显危险模式）
- `api.url_template` 域名必须与 `entry_url` 同源或在白名单内，防 SSRF

### 5.3 Run（小样试跑）

**输入**：candidate Spec
**动作**：通用引擎按 Spec 只跑 **1~2 个系列**（发现阶段全量发现，但规格抓取只取前 N 个型号）。
**输出**：小样抓取结果 + 一条临时审计记录（不写正式库，写 sandbox）。

### 5.4 Verify（校验打分）

用分层校验，任何一层不过即判不达标，并生成结构化失败原因（供 Repair 用）。

| 层级 | 检查项 | 判据示例 |
|---|---|---|
| L0 结构 | Spec 合法、选择器可编译 | schema 通过 |
| L1 发现 | 发现型号数 > 0，且与页面可见卡片数量级一致 | `discovered >= 1` 且不畸小畸大 |
| L2 连通 | 抽样型号 URL 真能打开、非 404 | 抽 3 个，≥2 个 200 |
| L3 规格质量 | 规格行数 ≥ 阈值、键值非空率、字段像规格 | 行数 ≥ 8，非空率 ≥ 60%，命中尺寸/分辨率/HDMI 等关键词 |
| L4 守恒 | 发现 N 型号 → 落库 N 型号 | `entry_models == stored_models`（防 family 覆盖）|
| L5 期望（软） | 与官网标称数对比 | 有 `expected` 时给出差额告警，不硬否决 |

**打分**：各层加权得总分，设通过阈值（如 L0~L4 必须全过，L5 仅告警）。

### 5.5 Repair（修复）

不达标时，把「失败层级 + 失败样本（比如抓到的空表 HTML 片段、404 的 URL、字段对不上的键值）」结构化回喂 LLM，要求它**针对性改 Spec**（而非重写）。

- 最多 N 轮（建议 3~5，可配置）
- 每轮记录 Spec diff 和失败原因
- **收敛** → 进 Persist 前的人工审核
- **N 轮不收敛** → `status=needs_human`，打包保存现场：探测报告、每轮 Spec、每轮失败原因、DOM 快照、截图

### 5.6 Persist（存库 + 全量）

- 达标 Spec 经**人工审一眼**后 `status=approved`，存入 `adapter_spec` 表（带版本号）
- 引擎按 approved Spec 全量抓取，走**正式**审计和入库路径（复用现有 `save_spec_sheet` 等）

---

## 6. 数据存储设计

新增两张表（不动现有表）：

```sql
-- LLM 生成的适配器配置（带版本，可回滚）
CREATE TABLE adapter_spec (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL,          -- sony_uk
    region        TEXT,
    version       INTEGER NOT NULL,       -- 同 code 递增
    status        TEXT NOT NULL,          -- candidate|approved|deprecated|needs_human
    spec_json     TEXT NOT NULL,          -- AdapterSpec 全文
    score         REAL,                   -- 最近一次校验总分
    generated_by  TEXT,                   -- 模型 + 探测报告引用
    approved_by   TEXT,                   -- 人工审核人
    created_at    TEXT NOT NULL,
    UNIQUE (code, version)
);

-- 闭环每一轮的过程留档（排查/审计用）
CREATE TABLE onboard_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL,
    attempt       INTEGER NOT NULL,       -- 第几轮
    phase         TEXT NOT NULL,          -- probe|generate|run|verify|repair
    passed        INTEGER,                -- 0/1
    score         REAL,
    detail_json   TEXT,                   -- 失败层级、样本、diff
    created_at    TEXT NOT NULL
);
```

`spec_entry_audit`、`spec_series_status` 继续作为**正式抓取**的审计来源，同时被 Verify 阶段读作评分依据。

---

## 7. 命令行接口

```bash
# 首次接入：跑完整闭环
py -3.12 -m overseas.cli spec-onboard --code sony_uk --entry <url> [--region uk] [--max-repair 5]

# 日常重跑：读 approved Spec 全量抓（不调 LLM）
py -3.12 -m overseas.cli spec-crawl --code sony_uk

# 健康探测：小样验证已存 Spec 是否还有效
py -3.12 -m overseas.cli spec-health --code sony_uk [--all]

# 人工审核 candidate → approved
py -3.12 -m overseas.cli spec-approve --code sony_uk --version 3
```

`spec-crawl` 需扩展：若 `code` 对应手写适配器则走旧路径，若只有 approved Spec 则走通用引擎——两者并存，平滑迁移。

---

## 8. 分阶段落地计划

| 阶段 | 内容 | 是否用 LLM | 价值 | 风险 |
|---|---|---|---|---|
| **一** | 「超时→读未成功系列→自动续跑」脚本 | 否 | 立即消掉当前最痛人工点 | 极低 |
| **二** | 把 1 个现有适配器反抽象成 AdapterSpec + 通用引擎，跑出与手写一致的结果 | 否 | 验证"配置驱动"可行，打地基 | 低 |
| **三** | 接 LLM 做 Generate/Repair，在**已知好抓的站**上试，对比手写 | 是 | 验证闭环收敛性 | 中 |
| **四** | 扩展到新站，保留"不收敛转人工"兜底 | 是 | 实现目标 | 中高 |

**强烈建议从阶段一或二起步**，先拿到不依赖 LLM 的确定性收益，再逐步引入不确定性。

---

## 9. 可能存在的问题与风险（重点）

### 9.1 技术风险

| 风险 | 具体表现 | 缓解措施 | 残留风险 |
|---|---|---|---|
| **强反爬** | Cloudflare、指纹检测、验证码、行为风控 | 浏览器指纹/代理/等待策略；命中即标记"需人工/放弃" | LLM 改选择器无法解决，部分站根本抓不了 |
| **"找全"判定不可靠** | 无官网总数时，靠滚动不增长/翻到空页判断"抓全了"；LG 标 35 实抓 34 | L5 期望校验给差额告警；保留人工确认 | 机器判不出差的是哪个，仍需人看 |
| **语义抓错** | 抓到一张表但不是规格表；键值错位 | L3 字段级校验（值域、单位、规格关键词、与已知型号交叉核对）| LLM 仍可能被相似结构骗过 |
| **隐蔽守恒 bug** | 同名系列/型号覆盖（Samsung family），"看着齐了"实缺 | L4 守恒校验强制 `发现数==落库数` | 新型覆盖模式可能绕过现有校验 |
| **站点改版** | 老 Spec 选择器失效 | spec-health 定期探测 + 成功率骤降自动重生成 + Spec 版本回滚 | 改版当天到下次探测之间有数据缺口 |
| **XHR 接口不稳定** | 官方接口加鉴权/签名/限频 | 探测记录接口鉴权特征；接口失败回退 DOM 模式 | 带动态签名的接口难自动复现 |
| **动态渲染时机** | 规格懒加载，抓取时表未渲染 | `page_wait` 锚点 + 重试；Verify 抓到空表判失败触发 Repair | 极端异步页面等待策略难自动收敛 |

### 9.2 LLM 相关风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| **幻觉选择器** | LLM 编造页面上不存在的 class/接口 | schema 校验 + 小样立即试跑证伪 |
| **不收敛** | N 轮反复改仍不达标，甚至反复横跳 | 硬性轮次上限 + 记录 diff 检测震荡 + 转人工 |
| **成本不可控** | 复杂站多轮重试，token 消耗大 | 小样验证、精简 DOM、轮次上限、按 code 缓存 |
| **过拟合小样** | 前 2 系列过了，全量却大面积失败 | 全量后再做一次全量审计，不达标降级为 candidate |
| **提示注入** | 页面恶意文本诱导 LLM 输出危险配置 | 探测内容当纯数据；配置有域名白名单、无代码执行面 |
| **非确定性** | 同输入两次生成不同 Spec | approved Spec 落库固定，日常重跑不再调 LLM |

### 9.3 数据正确性风险

- **误判成功**：闭环若校验不严，会把"抓了一部分"当"全成功"。**这是最危险的**——比抓不到更糟，因为会污染交付数据。对策：L4 守恒校验必须硬性、全量后二次审计、`expected` 差额显式告警。
- **跨站/跨品牌污染**：通用引擎复用逻辑，若身份键设计不当可能串数据。对策：所有入库带 `code`/`region` 维度，沿用现有 region 边界校验。

### 9.4 合规与工程风险

- **合规**：自动化扩展到任意站，可能触碰 robots、ToS、反爬对抗的灰色地带。**必须有站点白名单/黑名单机制**，不是给个 URL 就无脑抓。
- **维护复杂度**：引入 LLM 客户端、探测器、校验器、闭环控制器后，系统复杂度显著上升，调试链变长。对策：每阶段留档（onboard_run 表），失败现场可复现。
- **依赖外部模型**：模型 API 变更/限流/涨价影响可用性。对策：Generate/Repair 与执行解耦，模型只在 onboard 时用，日常抓取零依赖。

### 9.5 一句话结论

**能把新站接入从"写几百行代码"降到"给 URL + 审配置"，日常重跑基本零人工；但"任意网站全自动零人工"做不到**——强反爬、找全判定、语义正确性、合规这四道坎会长期需要人。最该警惕的不是"抓不到"，而是**闭环误判成功导致的数据污染**，所以守恒校验和全量二次审计是不可省的红线。

---

## 9.6 实施进展

- **阶段一（已完成）**：`scripts/refresh_spec_resume.py` —— 读入口审计与系列状态自动算出未成功系列并定向续跑，多轮不收敛即停并提示人工。加拿大线 dry-run 验证正确（五品牌 0 pending，仅 LG 8 个真实失败）。顺带修复 `spec_identity.family_series_key` 折叠内部空白导致 Samsung 系列被误判未成功的 bug。
- **阶段二（已完成 POC）**：
  - `docs/specs/philips_ca.spec.json` —— 把手写 Philips CA 适配器抽象成 AdapterSpec 配置。
  - `overseas/generic_spec.py` —— `GenericSpecAdapter`，读 AdapterSpec 执行发现/解析，复用现有 `ca_spec_common`、`spec_parser`、`open_dom`。
  - 离线等价性验证（发现映射、入口审计计数、规格三元组逐字段一致，正确过滤非 TV、正确归并同系列多尺寸）：**PASS**。
  - 结论：**配置驱动可行**，能忠实复现手写适配器，可作为阶段三（接 LLM 生成 Spec）的执行地基。
  - 当前引擎覆盖 `discover.mode=dom_anchor` + 可选 JS 分页片段 + 内嵌 JSON / 通用规格两种解析；`xhr_api`、`scroll`、`query_param` 分页等模式为预留扩展点，未实现时显式报错而非静默降级。

## 9.7 借鉴 Crawl4AI 的设计（不引入框架）

Crawl4AI 是成熟的 AI 爬虫框架，但它**全异步 + Playwright 依赖**，与本项目同步架构（urllib + 现有 BrowserFetcher）冲突，直接引入代价大。因此**只借鉴其两个设计思想，不引入依赖**：

1. **`generate_schema()` 思路（最高价值）**：给一个真实样本页 HTML + 自然语言查询，让 LLM 一次性生成稳定的抽取 schema（选择器），后续抽取走高速引擎、不再调 LLM。这与本方案「LLM 产配置、引擎执行」完全一致。
   - **改进本方案**：`spec_generator` 除了吃"探测报告摘要"，新增能力——直接吃**真实型号页 HTML 样本**生成 `spec` 抽取配置，选择器更贴合实际 DOM（借鉴 crawl4ai 用样本页而非人工摘要）。
   - **保留本方案优势**：一次性成本，日常抓取零 LLM；生成的 Spec 经守恒/字段校验后才入库。

2. **HTML→Markdown / Pruning 预处理**：喂 LLM 前先降噪（去 script/style、剪枝、截断），省 token、提准确率。
   - **改进本方案**：Probe/生成阶段喂 LLM 前统一用 `prune_html()` 精简，对应本方案第 5.1 节"精简 DOM"的落地。

**不借鉴的部分**：Crawl4AI 的异步 crawler、Playwright stealth、其自有 schema 格式——本项目已有等价或更适合的同步实现（BrowserFetcher、GENERIC_SPEC_JS 多策略抽取、AdapterSpec）。

对照你给的三层对比：本项目在存储/调度/API/导出/翻译上均优于或独有；Crawl4AI 独有的正是「LLM 抽取 + Markdown 预处理」两项——本节把这两项以最小代价补齐到本项目，而非替换框架。

## 9.8 阶段三进展与联网阻塞记录

**已完成（代码链路）**：
- `overseas/llm_client.py`：通用 Chat 客户端（复用 xinghai 端点/鉴权/SSE 解析，凭据仅从环境变量读），`chat()` 带重试。
- `overseas/spec_generator.py`：
  - `generate_spec(probe_report)`：探测报告 → AdapterSpec，含 schema + 安全校验（mode 白名单、正则可编译、域名同源防 SSRF、snippet 登记校验），不达标回喂修复，最多 N 轮。
  - `generate_spec_from_sample(sample_html)`：**借鉴 Crawl4AI generate_schema**，喂真实型号页样本生成 `spec` 抽取配置。
  - `prune_html()`：**借鉴 Crawl4AI Markdown/Pruning**，喂 LLM 前 HTML 降噪。
- 上述均 `py_compile` 通过；离线校验函数（schema/安全）逻辑自洽。

**联网阻塞（环境问题，非代码问题）**：
- 端点 `https://inner-apisix.hisense.com/higpt-new/v1`：DNS 解析正常（10.19.64.234），TCP 443 秒连，TLSv1.2 握手成功。
- 但**发出 POST 请求后固定 5 秒被重置**（`SSL: UNEXPECTED_EOF_WHILE_READING`），明文 HTTP 侧返回 **502**。
- 结论：HiGPT 网关到上游服务此刻不通，或当前来源被网关限制。凭据格式、鉴权头、user_key 查询参数、SSE 解析均与项目已验证的 `xinghai_translation.py` 一致，**代码链路正确，等待网关可用后即可端到端联调**。
- 复联调方式（网关恢复后）：设 `OVERSEAS_XINGHAI_API_KEY` / `OVERSEAS_XINGHAI_USER_KEY` 环境变量后，用一份探测报告调用 `generate_spec`，再用 `generic_spec.build_generic_adapter` 离线复现发现结果比对手写适配器。

## 9.9 阶段三端到端跑通（DeepSeek 公网验证）

**状态：闭环 Generate→Verify→Repair→复现 已用真实 LLM 跑通（RESULT PASS）。**

- **LLM provider 多路支持**：`overseas/config.py` 新增 `llm_settings()`，支持 `OVERSEAS_LLM_PROVIDER`：
  - `xinghai`（内网 HiGPT，user_key 走查询参数，默认流式）
  - `deepseek`（公网 `https://api.deepseek.com`，默认非流式）
  - `openai_compatible`（任意 OpenAI 兼容端点）
  - 凭据全部走环境变量（`OVERSEAS_LLM_API_KEY` 等），不入代码/文档/导出。
- **`overseas/llm_client.py` 重写**：openai SDK 优先、urllib 兜底；SDK 默认非流式（更稳），xinghai 自动流式。
- **端到端结果**（provider=deepseek, model=deepseek-chat，探测报告=Philips CA）：
  1. Generate：生成完整 AdapterSpec；
  2. Repair：第一版 `model_url_regex` 为空 → 闭环回喂 → 修正为 `/c-p/([^/_]+)`（与手写版不同写法但等效）；
  3. Verify：schema + 安全校验通过；
  4. 复现：用生成的 Spec 跑 `generic_spec` 引擎，正确发现 OLED760(2)/PUS8600(2)、过滤 soundbar、2 系列 4 型号，与手写适配器一致。
- `prune_html` 降噪、`generate_spec_from_sample` 均验证有效。

**内网 xinghai 网关阻塞仍在**（9.8）：deepseek 公网通、xinghai 内网当前连不上，二者互不影响；等内网环境即可切 `OVERSEAS_LLM_PROVIDER=xinghai`。

**工程教训**：PowerShell 管道执行 Python 时会吞掉 stderr，中文异常/traceback 不显示，表现为"进程静默退出"。排查此类问题必须加 `2>&1`。已记录以免后续误判为代码 bug。

## 9.10 对比测试：hanzheng 的 gen_schema 策略（接 DeepSeek 实测）

从内网 GitLab `dev_hanzheng` 分支下载了另一套同源实现（保存在 `爬取项目/tvcraw_ref/`，与本项目隔离）：`llm_schema_gen.py` + `schema_validator.py` + `gen_schema.py`，面向**零售站商品页字段抽取**（生成 `schema.json`，含 `interact` 交互段）。

**离线校验测试（不联网）**：`schema_validator` 三用例全过——合理 schema 100 分、坏 schema 0 分 FAIL 带失败清单、结构错误直接 0 分。其 transform（品牌清洗/价格解析/货币/尺寸/评分）与命中+语义+权重打分正确可用。

**闭环测试（接 DeepSeek，monkeypatch 其 `_call_llm`）**：用一段"价格/规格在页尾、超出 30K 截断"的长 HTML 对比两种喂法：

| 喂法 | 结果 | 说明 |
|---|---|---|
| A 原始 HTML（内部截断 30K） | **94 分 PASS，2 轮收敛** | 第 1 轮 68 分（Amazon 专属选择器没命中）→ 失败清单回喂 → 第 2 轮 94 分。**证明 generate→validate→repair 闭环有效** |
| B prune_html 压缩后再喂 | **0 分 FAIL** | 压缩破坏了 `id`/`class`/结构线索，LLM 只能生成通用猜测选择器（`h1`/`.price`），全部落空 |

**关键洞察（负面结果，价值高）**：
1. hanzheng 的闭环本身有效，短板确实在文档 6.2 说的"喂什么给 LLM"，不在闭环逻辑。
2. **但简单的整页 `prune_html` 压缩会帮倒忙**——它是为 SPEC 内嵌 JSON 场景设计的，用在需要**精确 CSS 选择器**的商品页抽取上，反而丢掉了 LLM 生成选择器所需的 DOM 线索。
3. 正确的 P0 方向印证了 hanzheng 文档 6.3 的进阶做法：**"压缩版当地图定位目标 + 原始 DOM 片段当施工面生成选择器"**，而非"整页压缩后喂"。`prune_html` 只适合定位阶段，不能替代 selector 生成的原始 DOM 输入。

**两套方案定位互补**：hanzheng = 零售站商品页字段（schema.json + interact + 更细的命中/语义打分）；本项目 = 品牌官网 SPEC 发现解析（AdapterSpec + generic_spec + 守恒/安全校验 + 多 provider）。可整合：把 hanzheng 的**命中率+语义+权重打分**吸收进本项目 Verify；把本项目的**多 provider（deepseek 已验证）+ 守恒校验**补给 hanzheng 那套。

## 9.11 吸收 hanzheng 的命中率+语义打分（面向发现阶段"找全型号"）

把 hanzheng schema_validator 的**"在真实 HTML 上打分"**思路吸收进 `spec_generator`，但**维度改造为发现阶段语义**（他们打商品页字段 title/price；我们打"发现结果"）。

**新增 `score_spec_on_html(spec, entry_html, ...)`**，四个打分维度：
| 维度 | 权重 | 含义 |
|---|---|---|
| link_selector 命中 | 3 | 入口 selector 在真实页面命中产品链接数 |
| 型号可提取且格式合理 | 3 | 型号正则能从链接提出型号，且字母+数字混合（语义校验）|
| 找全率(vs expected) | 2 | 发现型号数 / 期望数，量化"**找全没有**" |
| 型号页规格可解析（可选）| 2 | 给型号页样本时，spec 段能出 ≥3 条规格行 |

打分 = 命中率×60% + 语义/覆盖×40%（对齐 hanzheng 加权），并给**发现阶段硬指标否决权**：link 未命中 / 型号提不到 / 找全率<80%，任一直接判不通过（比 hanzheng 的"扣分"更强，因为找全是发现阶段核心目标）。

**接入 `generate_spec`**：新增可选 `entry_html` 参数，静态校验（validate_spec）通过后，若提供真实 HTML 再做实测打分，不达标同样回喂 LLM 修复。形成"静态合法 + 真实页面可发现且找全"双层校验。

**借鉴意义测试（离线，PASS）**：
| 场景 | 静态校验 | 实测打分 | 说明 |
|---|---|---|---|
| 好 Spec | 通过 | 100 分 PASS | 命中 5 链接、5 型号、找全率 100% |
| 选择器语法合法但页面无此元素 | **放行** | **0 分拦截** | **静态校验永远查不出，实测能查出** — 核心借鉴价值 |
| 期望 8 实际发现 5 | 放行 | 75 分但被找全率否决 | 量化"找全没有"，静态校验无此能力 |

**结论**：吸收有明确价值——静态校验只能保证"配置合法"，无法保证"在真实页面上真能发现型号、找全型号"。实测打分补上了这一层，且失败原因（哪个选择器没命中、找全率多少）可直接回喂 LLM 修复，使发现阶段的 schema 生成从"能编译"进化到"经真实页面验证能找全"。这与 hanzheng 用真实 HTML 打分驱动 LLM 修复的思路一致，只是把评分对象从"字段抽取"换成了"型号发现与找全"。

## 9.12 发现阶段自动化闭环打通（Probe → Generate → Verify → 直接发现）

目标（用户明确）：进入陌生电视站 → 自动摸清结构（含分页/view more）→ LLM 总结成 schema → 按 schema 直接抓全站 TV。补上最后缺口 **Probe**，全链路跑通。

**新增 `overseas/spec_probe.py`**：
- `build_probe_report(entry_url, entry_html, ...)`：在真实 DOM 上用一组通用候选选择器排名，选出**最佳产品链接选择器**（命中最多且过滤 soundbar/support 等非电视），归纳 **URL 路径模式**，对比初始/滚动后/加载更多后的链接数**判断分页方式**（none/scroll/click_more_or_paginate），输出结构化探测报告。
- `probe_site(fetcher, entry_url, ...)`：在线模式，用现有 BrowserFetcher + open_dom（复用其 scroll_until_stable 能力）打开页面、滚动到稳定、采集渲染后 HTML。
- 离线模式：直接传 entry_html，测试/无浏览器可用。

**`spec_generator` 增强**：
- `build_generate_messages` 现在把 probe 的**探测线索**（最佳选择器、URL 样本、URL 模式、分页提示）提炼进 prompt，指导 LLM 填 `link_selector`/`model_url_regex`/分页。
- 新增 `onboard_from_html(entry_url, entry_html, ...)`：一站式 Probe→Generate→Verify(实测打分)→Repair 编排，返回 `{probe_report, spec, problems, passed}`。

**端到端测试（陌生站 + DeepSeek，PASS）**：构造一个从未见过的电视总览页（8 个产品卡 + soundbar/support 干扰）：
1. Probe 自动选出 `a[href*='/p/']`（命中 8，过滤干扰），归纳模式 `/en/tvs/<MODEL>/p/<MODEL>`；
2. DeepSeek 据线索生成 `link_selector` + `model_url_regex: /p/([A-Za-z0-9-]+)`；
3. 实测打分 + 找全率校验：8/8 找全，passed；
4. 用生成的 schema 跑引擎实际发现：`series=8 models=8` 全部找到。

**意义**：发现阶段"进陌生站→摸结构→出 schema→找全型号"闭环已在离线端到端验证通过。真实站还需 BrowserFetcher 联网跑 `probe_site`（分页/view more 的动态增长判断依赖真实渲染），但逻辑链路已完整、各环节均有校验兜底（实测命中 + 找全率否决）。

**当前发现阶段完整能力图**：
```
入口URL → spec_probe.probe_site(浏览器,摸结构/分页)
        → build_probe_report(最佳选择器/URL模式/分页方式)
        → generate_spec(LLM总结 + 静态校验 + 真实HTML实测打分 + 找全率否决 + 回喂修复)
        → AdapterSpec(schema) → generic_spec 引擎直接发现全站型号 → 逐型号抓 SPEC
```

## 9.13 prune_html 升级（借鉴 Crawl4AI preprocess_html_for_schema）

实读 Crawl4AI 源码后确认可借鉴的**唯一高价值点**：其 `preprocess_html_for_schema` 的**重复卡片去重**。据此把 `prune_html` 从正则版升级为 **lxml 解析树版**：
- 去 `<head>` 整段 + script/style/svg 等非内容标签；
- 只保留 `id/class/name/type/value/href/data-*`（生成选择器有用），长属性值/长正文截断；
- **重复卡片去重**：同 `(tag, class, 文本hash)` 的元素只留第一个——列表页 N 个同构产品卡喂 LLM 留 1 个样例即可；
- lxml 不可用时回退正则版 `_prune_html_regex`（保底不崩）。

**回归测试（离线，PASS）**——重现 9.10 翻车场景（112K 字符：1200 个重复导航卡 + 页尾产品/价格/规格）：
| 版本 | 输出大小 | 页尾关键元素保留 |
|---|---|---|
| 旧正则版（截断 30K）| 30,021 字符 | **0/4**（页尾全丢，正是 9.10 实验 B 翻车主因）|
| 新 lxml 去重版 | **418 字符** | **4/4**（productTitle/prodDetails/a-offscreen/65U8N 全保留）|

**结论**：升级直接修复 9.10 的负面结果——去重把 1200 个重复卡压成 1 个，页尾生成选择器所需的关键元素全部进入 LLM 窗口，压缩比从"112K→30K 截断丢内容"变为"112K→418 保内容"。解决了 Crawl4AI 文档 6.2 指出的"整页只喂到约 1%"痛点，且纯 lxml 实现、不引框架。

**明确不借鉴**：PruningContentFilter/BM25（面向正文提取，会误删规格表）、LLMExtractionStrategy（每页调 LLM 抽取，与本项目"LLM 只生成 schema、抽取走高速引擎"路线冲突且成本高）、异步框架/Playwright stealth（与同步架构冲突）。

## 9.14 真实站联网端到端验证（Philips CA，25/25 全找到，PASS）

在真实浏览器 + 真实站点上跑通完整发现闭环。环境：Playwright chromium（本次现装，
解决了残留 `__dirlock` 孤儿锁导致安装失败的问题）+ DeepSeek。

**真实站结果（Philips CA）**：
| 步骤 | 结果 |
|---|---|
| Probe 打开真实站 | 渲染 HTML 1.3M 字符 |
| 结构识别 | `load_hint=paginate_replace`（正确判定为**替换式分页站**）|
| LLM 生成 schema | `link_selector=a[href*='/c-p/']`、`model_url_regex=/c-p/([^/]+)/`、选中 `philips_paginate` 分页片段 |
| 引擎执行发现 | **series=11 models=25，找全率 100%**（与官网真实数量一致）|

**过程即价值（几次 FAIL 是逐步暴露真实难点，非 bug）**：
1. 第一次：Probe 只静态采集首屏，发现 12/25，实测打分**如实拦截**（找全率 48%）——证明找全率否决有效，没让"抓一半"蒙混过关；
2. 诊断发现 Philips 是**替换式分页**（`aria-label='Show page N of results'`，点击换掉当前 12 个而非追加），通用"点 next 直到不增长"对替换式渲染无效；
3. 给 Probe 加**分页控件检测** `_detect_pager`：即使链接数不随点击增长，只要检测到编号页码/下一页按钮，就判定为分页站并汇报 `paginate_replace`，让 LLM 知道选累积式分页片段；
4. 修正找全率判定口径：分页站不用首屏 HTML 卡分，改用**引擎执行分页片段后的实际发现数**判定——这才是正确的"找全"度量。

**Probe 增强**：`probe_site` 新增 `try_load_more`（两趟：仅滚动 vs 额外点击通用"加载更多/下一页"候选，对比链接增长）；`build_probe_report` 新增 `_detect_pager`（编号页码/下一页/加载更多控件检测）。

**明确的能力边界（务实结论）**：
- 通用 Probe 能**识别**"这是分页站"（滚动增长 / 标准 load-more / 编号页码 / 替换式分页四类）。
- 但**替换式渲染分页的累积抓取**需站点专属 JS 片段（如 `philips_paginate`）——通用引擎不自动生成可执行代码（守住"只产配置不产代码"安全线）。当前做法：Probe 识别是分页站 → LLM 从**已登记片段库**选匹配片段；若无匹配片段，如实报告"需人工补一个分页片段"，而非假装全自动。
- 因此系统真实价值：**标准站全自动；特殊分页站自动识别 + 复用已有片段，无匹配时精确告知人工补哪一块**——而不是号称任意站零人工。

**至此发现阶段自动化在真实站验证完成**：陌生站 URL → 浏览器摸结构（含分页识别）→ LLM 出 schema → 找全率校验（基于引擎实际发现）→ 得到可复用 schema → 之后按 schema 直接抓全站 SPEC。

## 9.13 通用分页片段鲁棒性升级（2026-09-15）：找全率 48%→100%

上文能力边界说"替换式分页需站点专属片段（如 `philips_paginate`），通用片段无匹配时需人工补"。本次把
**通用片段 `numbered_pages_generic` 本身做鲁棒**，使其对 Philips 这类**编号页码 + 替换式渲染**的分页也能自动翻到尽头，
不再依赖 LLM 恰好选中专用片段。边界因此上移：编号页码型分页现在**通用片段即可全自动找全**。

**背景问题**：一次 onboard 中 LLM 为 Philips CA 选了通用 `numbered_pages_generic`（而非专用 `philips_paginate`），
引擎只发现 12/25（48%）。

**真实根因（诊断推翻了初判）**：初判以为是"maxPage 只首屏探测一次、翻不到尽头"，但决定性证据表明不是——
- 首屏其实有 122 个 `/c-p/` 链接，去重后只 12 个型号（=第 1 页），两种 `model_url_regex` 提取都是 12，排除正则/归并问题；
- 分页控件真实 `aria-label='Show page N of results'`（每页 ~9 个、共 3 页 ≈ 25）；
- 致命 bug 在 `pageBtn(n)`：片段是 Python raw string，写成 `new RegExp('\\\\bpage\\\\s+'...)`，四个反斜杠在 JS 里
  求值为 `\\b`（匹配**字面反斜杠**），页码按钮**永远匹配不到**、翻页全失败。Node 实测四反斜杠版对真实 aria-label
  返回 false、双反斜杠版返回 true。

**修复**（`overseas/generic_spec.py` 的 `_NUMBERED_PAGES_GENERIC_JS`）：
1. `pageBtn` 正则 `'\\\\b'`→`'\\b'`（双反斜杠，JS 求值为正确的 `\b`），并加注释警示三层转义；
2. 新增 `scanPages()` 每翻页重扫页码 + `visited`/待访问队列**翻到尽头**（连续 2 轮不增长停，guard≤80）；
3. `clickAndWait` 命中增长后两段收割（700+500），对 Next.js 替换渲染更稳；无编号页码回退"点下一页"（≤60）。

**结果**：隔离"片段"单一变量（写死 `numbered_pages_generic`）跑真实 Philips CA，`series=11 models=25`（**100%**，官网标称一致）。
回归：`py_compile` + `node --check` + 离线正则用例（编号/多位数/下一页回退）全过。

**沉淀教训**：JS 片段嵌在 Python raw string 里时，`new RegExp(字符串)` 的反斜杠要按"Python 字面→JS 字符串→正则"
三层数清；片段库改动后应过 `node --check` + 针对真实 `aria-label` 的匹配自测，防"语法对但运行时正则失效"。
仍守"只产配置不产代码"：本次是完善已登记片段库内的片段，非 LLM 生成代码。

## 10. 建议的下一步

请在以下选项中拍板，确认前不改动任何现有抓取代码：

- **选项 A（阶段一）**：写 `scripts/refresh_spec_resume.py`，把"读未成功系列→定向续跑"固化，立即消掉超时续跑的人工点。
- **选项 B（阶段二）**：选一个结构最规整的品牌（Sony/Philips），做 AdapterSpec + 最小通用引擎 POC，跑出与手写一致的结果。
- **选项 C**：先补充/修订本方案文档某些章节（如字段定义、校验规则细化），再动代码。

> 备注：现有全线抓取代码已于 2026-09-15 备份至 `backup_code/抓取代码备份_全线_20260915_104104.zip`。

## 11. 发现阶段样例扩覆实测（2026-09-15 续3）
目标：用更多新站样例检验发现阶段自动化的适用面，暴露探测器/片段库缺口并"只产配置"地完善。

### 11.1 本次实测的两个样例站
- **秘鲁 Hisense**（`https://www.hisense.com.pe/tv`）——★端到端全自动成功★
  - 结构：**静态 lineup 一次列全**（`static_unique_links=75`、`load_hint=none`、滚动/加载更多后链接数无增长），
    产品 URL 规整（`/tv/...-55ur8sg`）。这正是此前"最该先自动化、却从未端到端实测过"的核心结构。
  - `spec-onboard` 探测→LLM 生成→静态校验+实测打分全通过；`--verify-discover` 在线实抓 **13 系列 36 型号**。
  - LLM 产出的 `model_url_regex = /tv/[^/]*-([0-9]{2}[a-z0-9]+)$` 正确框住型号；`discover.mode=dom_anchor` 无分页。
  - 结论：**"静态 lineup 列全"结构现可经 dom_anchor 全自动 onboard，无需手写适配器。**
- **Hisense US**（`https://www.hisense-usa.com/category/televisions`，期望 181）——暴露缺口 + 记录为超边界站
  - 结构：Wix 站，产品链接 `/product-page/<slug-型号>`，型号写法混乱（`75u7sg`/`tv43qd40r`/`40qd40r`），
    混入投影/音箱/空调需过滤；产品靠 Load More 动态追加。
  - 暴露并修复两处通用探测器缺口（见 11.2）。修复后 best_selector 正确切到 `/product-page/`。
  - 仍存固有边界（**不强凑**）：Load More 未被通用点击候选点开（`load_hint=none`），首屏仅 29~64 链接远少于 181；
    型号 slug 过于杂乱，通用正则难可靠提取。→ **Hisense US 本质仍需手写适配器**，属 dom_anchor 边界外。

### 11.2 修复的通用探测器缺口（仅改探测器评分逻辑，不改引擎能力边界）
均在 `overseas/spec_probe.py`：
1. **候选选择器补全**：`_LINK_SELECTOR_CANDIDATES` 增加 `a[href*='/product-page/']`（Wix 建站通用产品路径）。
2. **best_selector 排序缺陷修复（关键）**：原排序只按"命中数"降序，导致导航泛选择器 `li a[href]`
   （命中 66~94 个，多为分类/其它品类/导航）**淹没**真实产品选择器 `a[href*='/product-page/']`（命中 29 个）。
   改为**"型号纯度"优先**：
   - 新增 `_looks_like_model_url()` + `_MODEL_SLUG_RE`（要求末段含"字母紧邻≥2位数字"，如 `75u7sg`/`65OLED759`；
     借此排除 `4k-uled`/`4k-uhd`/`smart-tv-platforms` 这类只有孤立单数字的导航词）；
   - 排序主键改为 `model_like_ratio`（纯度），并设 `model_like≥3` 门槛，避免只命中 1~2 个的选择器凭 100% 纯度虚高；次键才是命中总数。
   - 效果验证：Hisense US 上 `/product-page/` 纯度 0.97 胜出 vs `li a[href]` 纯度 0.42；秘鲁 Hisense 上
     `a[href*='/tv/']` 纯度 0.69 胜出 vs `li a[href]` 纯度 0.56。

### 11.3 回归与守则
- **离线判据单测**：`_looks_like_model_url` 在 Philips `/c-p/`、秘鲁 Hisense `/tv/`、Hisense US `/product-page/` 型号页均判 True，
  在各站分类/导航页（`/c-m-so/tv/latest`、`/televisions/4k-uled` 等）均判 False——全部正确。
- **Philips CA 实抓回归**：`spec-crawl --site philips_ca`（独立 sandbox db）结果 **系列 11 型号 25 终态 stable，ok=11 fail=0**，
  与既有 100% 一致——排序改进**未破坏**任何既有实抓（手写适配器与 generic_spec 引擎均未改动）。
- `py_compile` 通过：`spec_probe.py`/`spec_generator.py`/`generic_spec.py`/`cli.py`。
- 仍守"只产配置不产代码"：本次仅改探测器的选择器候选与排序评分逻辑，未新增 JS 片段、未扩发现模式白名单。

### 11.4 沉淀经验
- 发现阶段选 best_selector **不能只看命中数**：品牌站导航/页脚常用宽泛的 `li a[href]`，命中数天然更高但纯度低；
  应以"命中链接里像型号页的比例"为主排序信号，才能在电视总览页稳定选中真正的产品链接选择器。
- "静态 lineup 列全 + 型号 URL 规整"是 dom_anchor 覆盖最好的结构，应作为通用引擎优先保证的场景。
- Wix 类站（`/product-page/`）产品链接虽可识别，但 Load More 的通用点击与杂乱 slug 型号提取仍是边界，宜手写适配器。

## 12. 日本线样例验证：REGZA / Sony BRAVIA（2026-09-15 续4）
目标：用日本线站点（有手写适配器期望值可对照）继续检验发现阶段自动化，暴露并"只产配置"地完善探测器。

### 12.1 两个日本线样例
- **REGZA JP**（`https://www.regza.com/tv/lineup`）——端到端全自动成功
  - 结构：单级 lineup（懒加载注入），系列链接 `/tv/lineup/<系列>`、SPEC 页 `/tv/lineup/<系列>/spec`，系列码规整（`e350m`/`x9900r`/`zx3s`）。
  - `spec-onboard --verify-discover` 实抓 **39 系列 39 型号**（当前 lineup 全部在售+历史系列，全部规整、无过抓无漏抓）。
  - 离线复现验证：`[class*='product']`、`a[href*='/tv/lineup/']`、`[class*='card']` 三选择器抓到的系列**完全一致**，
    因 `model_url_regex=/tv/lineup/([^/?#]+)` 已过滤非 lineup 链接。
- **Sony BRAVIA JP**（`https://www.sony.jp/bravia/lineup/`）——找全率 **10% → 80%**（修复后 8/8 当前在售 BRAVIA 电视）
  - 结构：静态列全（JS 延迟注入），产品链接 `/bravia/products/<型号>/`，型号写法特殊（`K-XR90M2`/`KJ-X81L`/`XRJ-A95L`）。
  - 暴露 3 个缺口（见 12.2），修复后 LLM 生成干净 schema、实抓当前 lineup 全部 8 款。

### 12.2 修复的 3 个通用缺口（全在探测器/prompt，只产配置不产代码）
1. **候选选择器补精确项**（`overseas/spec_probe.py`）：
   - 补 `a[href*='/bravia/products/']`——Sony 全站产品共用 `/products/`，宽泛候选会混入耳机（WH-1000XM6）、
     相机（ILCE-7RM6）、手机、FeliCa 等；电视专属候选凭 100% 纯度胜出，只留纯 BRAVIA。
   - 补 `a[href*='/tv/lineup/']`——REGZA 站有 `/tv/lineup/` 与 `/bd-dvd/lineup/`（蓝光录像机）；
     裸 `a[href*='/lineup/']` 会把 bd-dvd 混进来污染 URL 模式，故收窄到 `/tv/lineup/`。
2. **分页误判修复**（`overseas/spec_probe.py` 的 `build_probe_report`）：
   - 原逻辑只要 `has_next_button` 即把 `load_hint` 升级为 `paginate`；静态站（Sony BRAVIA lineup）的
     轮播/推荐区 next 按钮被误判成分页。
   - 改为：**仅"编号页码"（Show page N，`has_numbered_pages`）才是可靠分页信号**（升级 `paginate_replace`）；
     孤立 next 按钮而无编号页码时保持 `none`。
3. **prompt 明确禁分页**（`overseas/spec_generator.py` 的 `build_generate_messages`）：
   - `load_hint=none` 时新增 hint：首屏即全部，**不要**设 `discover.paginate`；即便报告里 `pager.has_next_button`
     为真，那多半是轮播按钮，请忽略。
   - 修复前 Sony 实抓 0~1：LLM 从报告的 pager 字段自作主张加了 `numbered_pages_generic`，实抓时点错 next
     按钮把页面导航走了；修复后不再加分页，实抓 8/8。

### 12.3 回归（均通过）
- **分页判定单测**：编号页码 HTML → `paginate_replace`（Philips 式分页站不受影响）；孤立 next HTML → `none`（Sony 式误判已消除）。
- **REGZA 离线复现**：修复后 best_selector=`a[href*='/tv/lineup/']`（纯度 0.875）精确胜出，`product_url_pattern=/tv/lineup/<MODEL...>`，
  sample 全干净（不再有 bd-dvd 污染）。
- **Philips CA 实抓回归**（独立 sandbox db）：**系列 11 型号 25 终态 stable，ok=11 fail=0**，与既有 100% 一致——分页判定改动未破坏编号页码分页站。
- `py_compile` 通过：`spec_probe.py`/`spec_generator.py`。

### 12.4 沉淀经验
- 宽泛路径候选（`/products/`、`/lineup/`）在"全站产品共用路径"或"多品类共用路径"的站点会混入非电视；
  应优先补**电视/品牌专属的精确候选**（`/bravia/products/`、`/tv/lineup/`），靠纯度排序自然胜出。
- 分页信号要**强区分**：只有明确的"编号页码"可靠；孤立的"下一页/next"按钮极易来自轮播/推荐区，
  不能仅凭它判定分页——否则会逼 LLM 给静态站加分页片段，反而点错按钮破坏发现。
- 探测报告里的原始信号（如 `pager.has_next_button`）会被 LLM 直接采信，故 prompt 需在 `load_hint=none` 时
  明确告知"忽略该按钮、不要分页"，避免 LLM 自作主张。

## 13. 完成度评估与剩余工作方向（2026-09-15 续5 汇总）

> 本节汇总当前整体完成度与后续待办，供排期与验收参考。评估基于对 `spec_probe.py`、
> `spec_generator.py`、`generic_spec.py`、`cli.py` 的代码核查与多站实测，不含未落地的设计。

### 13.1 完成度总览：约 70%（发现阶段 POC 已跑通，生产化未完成）

发现阶段的**核心闭环 Probe→Generate→Verify→Repair→Execute 已端到端可用**，对"结构落在支持范围内"
的站点能做到从陌生 URL 到抓全型号全自动。但距离"生产级、可无人值守扩站"仍有距离。

### 13.2 已完成（可用）
| 能力 | 状态 | 落地位置 |
|---|---|---|
| 五环闭环 Probe→Generate→Verify→Repair→Execute | ✅ 代码全通 | 5.1~5.6 各模块 |
| Probe 探测（滚动/点击/分页判断/纯度排序选择器）| ✅ 多站验证 | `spec_probe.py` |
| LLM 生成 schema + 静态校验 + 实测打分 + 回喂修复 | ✅ DeepSeek 验证 | `spec_generator.py` |
| 通用引擎按 schema 直接抓（dom_anchor + 内嵌JSON/通用解析 + 2 个分页片段）| ✅ 验证 | `generic_spec.py` |
| 安全护栏（模式白名单、片段库、域名防 SSRF、不产代码）| ✅ | 全链路 |
| 4 类结构实测（静态列全 / 懒加载 / JS 延迟注入 / 编号分页）| ✅ | 秘鲁 Hisense / REGZA / Sony / Philips CA |

### 13.3 剩余工作（按优先级）

**P0 — 覆盖面（决定"能自动化多少站"）**
1. **多级发现**：总览→系列→型号（松下 / 夏普）。当前 `dom_anchor` 只探单层，是最大能力缺口。
   需评估在引擎新增两级抓取或新 `discover.mode`（改的是引擎能力菜单，非给单站打补丁）。
2. **JSON-API / `xhr_api` 模式**：TCL / Samsung / LG 这类 SPA 靠接口出数据；`discover.mode=xhr_api`
   目前仅在代码里预留、未实现（命中即 `NotImplementedError`）。需实现"探测记录 XHR 接口 → schema 声明
   `api.url_template`/`list_path`/字段映射 → 引擎走接口发现"。
3. **`query_param` 分页**：LG 那种 `?firstResult=N` 翻页未支持，需在引擎实现该分页类型。

**P1 — 生产化闭环（决定"能否无人值守"）**
4. **onboard 全流程持久化**：第 6 节设计的 `adapter_spec` / `onboard_run` 表（存 schema、评分、每轮
   phase 审计）尚未落地——当前 schema 直接写 JSON 文件。缺它就没有"改版重生成 / 版本回滚 / 失败现场复现"。
5. **spec-health 定期探测**：第 3 节设计的健康探测未实现。站点改版后老 schema 失效，需定期小样探测 +
   成功率骤降自动触发 onboard 重生成。
6. **人工审核门（candidate→approved）**：第 5.6 / 7 节设计的 schema 状态流转与 `spec-approve` 命令未实现，
   当前"生成即用"。

**P2 — 稳健性与合规**
7. **在线抓取时机波动**：Sony JS 延迟注入时 verify-discover 曾抓到 0（靠去掉误加分页才稳定）；极端异步
   页面的等待/重试策略仍不够鲁棒，需强化 `page_wait` 锚点 + 重试收敛。
8. **站点白名单 / 黑名单准入**：第 9.4 节强调"不能给个 URL 就无脑抓"（robots/ToS 合规），该准入机制未实现。
9. **spec 段（规格解析）自动验证薄弱**：本轮验证集中在"发现 / 找全型号"，规格页解析（`embedded_json` 字段
   是否对得上、L3 规格质量校验）在新站上的自动验证还不充分，需补齐 Verify 的 L3 层实测。

**P3 — 文档订正（低成本）**
10. 第 3 节（及早期"目标拆解与现状"表述）曾把 **Probe 标为"缺"**，但 Probe 已于 9.12 补齐并经 9.14 / 11 / 12
    多站验证。相关旧表述应加更新标注，避免读者误以为 Probe 未完成。

### 13.4 一句话结论
**发现阶段 POC 目标已达成**——对常见静态 / 懒加载 / 单级分页的电视站，能全自动 onboard 并抓全型号，
安全边界清晰。**距离生产化差三块**：能力覆盖面（多级 / JSON-API）、闭环持久化与自愈（库表 / health / 审核门）、
合规准入。**建议下一步优先做 P0 的多级发现**（用松下 / 夏普验证），这是当前边界最明显、也最能扩大自动化
覆盖面的一环。
