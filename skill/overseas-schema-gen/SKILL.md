---
name: overseas-schema-gen
description: 海外零售站接入的统一 skill —— 生成抓取 schema 与适配器（AdapterSpec 官网发现 / RetailSpec 零售价评 / 运维协作），是探索新网站生成 schema/adapter 的唯一入口。当用户要求「接入新站」「生成 schema / 适配器」「诊断某站抓不全 / 匹配不到 / 价格抓不到」「全量运维」时使用。内部按分支委派，路径与命令全部在下方。
---

# 海外站全网统一接入 skill（schema / 适配器 / 运维）

> 单一入口，统一沉淀。接入新站的**唯一标准路径**，所有经验强制沉淀在这里。
> 分支细节在 `spec/`、`retail/`、`ops/` 子目录，本文件负责**选型分派 + 强制沉淀规则**。

---

## 核心架构：经验驱动的自动探索 → 硬编码抓取

```
                    ┌─────────────────────────────────────────────┐
                    │           skill（经验库·活文档）              │
                    │  cases.jsonl(546+条) + reference.md(850+行)  │
                    │  + playbook/(结构型套路) + selfcheck.py      │
                    └───────────┬───────────────────┬─────────────┘
                                │ 查相似站           │ 沉淀新经验
                                ▼                   ▲
  ┌──────────────────────────────────────────┐     │
  │   Agent自动探索（复用skill经验）            │     │
  │   probe.py探测 → 匹配playbook → 生成schema  │     │
  │   → selfcheck校验 → 小样实测 → 转正+沉淀    │─────┘
  └──────────────────┬───────────────────────┘
                     │ 产出: schema.json + adapter.py
                     ▼
  ┌──────────────────────────────────────────┐
  │   硬编码引擎（可复用，不需要再探索）        │
  │   spec_crawl() / s2_monitor_known()       │
  │   s4_review_incremental() / s1_search()    │
  └──────────────────┬───────────────────────┘
                     │ 产出: DB数据
                     ▼
  ┌──────────────────────────────────────────┐
  │   LangGraph全自动抓取 + DeepResearch报告   │
  │   (FLOW) 7节点并行引擎 → (REPORT) 6-Agent  │
  └──────────────────────────────────────────┘
```

**关键洞察**：skill是"探索经验库"，让agent探索新站时不用从零开始——
查cases.jsonl找相似站→复用其schema/adapter模板→改域名和选择器→校验→转正。
探索完成后生成schema.json+adapter.py，硬编码引擎直接调用，**不再需要探索**。

---

## 0. 先判断接的是什么（30 秒定方向，别往下深挖前先选对）

| 分支 | 场景 | 触发词 | 入口目录 | 产出 |
|---|---|---|---|---|
| **SPEC** | 电视品牌**官网 lineup 页** → 发现型号 → 规格 | 官网、lineup、发现型号、`spec-onboard` | [spec/](spec/SKILL.md) (AdapterSpec) | `docs/specs/<code>.spec.json` |
| **RETAIL** | 电商零售站 → 给定型号清单 → 抓价+网评 | 零售站、价格、网评、匹配不到、`crawl` | [retail/](retail/SKILL.md) (RetailSpec) | `docs/retail_specs/<code>.retail.json` |
| **OPS** | 已有适配器的全量运行 / 监控 / 修复 / 导出 | 全量、断点续抓、僵尸、污染、监控 | [ops/crawl-ops.md](ops/crawl-ops.md) | CSV/DB |
| **FLOW** | 探索完成后自动全量抓取编排 | LangGraph、全自动、并行抓取 | [flow/README.md](flow/README.md) | LangGraph引擎 |
| **REPORT** | 抓取数据生成调研报告 | 调研报告、对手分析、上市预警 | [report/README.md](report/README.md) | markdown报告 |

**分派规则**：新站先判断是官网 lineup（→SPEC）还是电商站（→RETAIL）；已有 schema 要运维 → OPS；
探索完成要自动全量抓 → FLOW；抓完数据要出报告 → REPORT；
不确定 → 按 结构 / robots / 规模 判断（表见下）。

**SPEC 判定**：品牌**官网**列出**电视 model** 的 lineup / 总览页（有系列→型号两级、或静态列全）。目的：找全某品牌所有电视型号。
**RETAIL 判定**：电商/零售站（Liverpool / BestBuy / Costco / Amazon 等），型号**已给定**，要按型号去定位价格与评价。目的：给定清单 → 抓价+网评。
**OPS 判定**：已有 schema 的站要跑全量、断点、监控、清理。

---

## 1. 铁律（所有分支通用，违反即返工）

1. **只产配置，不产代码。** 需要执行 JS 只能 `snippet_ref` 引用已登记片段，禁止内联 JS。
2. **不越过引擎能力边界。** schema 枚举值必须在对应引擎白名单（`spec` → `docs/AdapterSpec_schema规范.md` §6；`retail` → `overseas/retail_spec.py` `SUPPORTED_*`）。写了引擎不认的值 = 静默失效或报错。**越界 → 记为引擎缺口上报，不伪造配置。**
3. **校验不可跳过。** SPEC 必须 `validate_spec` + `score_spec_on_html`（找全率）；RETAIL 必须 `validate_retail_spec` + `retail-health` 小样实测（匹配/填充率）。"我看着对"不算通过。
4. **先读 robots.txt（RETAIL 尤其）。** 禁抓搜索但放行商品页 → 走 `retail/playbook/sitemap_discovery.md`，别硬用搜索。robots 结论必须抄进交付文档。
5. **`match.on_no_match` 必须 `skip`（宁缺毋滥）**，禁止 `first`。匹配准确率 = 100% 抽检零误匹配 才可交付。
6. **`no_item` 与 `failed` 严格分开。** 搜索页没渲染/被拦/出口 IP 不对 = `failed`；只有"站上确实没有"才是 `no_item`。分错 = 静默丢数据。
7. **短号型必须词边界匹配**（`a 4K` ≠ `A4K`）；尺寸列空先查 `product.size` 是否有 h1 标题兜底。
8. **抽样必须含至少一个该站确实没有的型号**：只验证"能命中"会漏掉误命中。
9. **找全率纪律**：`expected.model_count`/`series_count` 务必填（去官网数一遍），不填 = 关掉找全率闸门。
10. **改动引擎白名单 → 同步更新 selfcheck 对应常量**（见各分支 selfcheck.py）。

---

## 2. Agent自动探索流程（skill经验驱动，6步标准化）

> **核心原则**：Agent探索时先查skill经验库（cases.jsonl+reference.md+playbook），
> 找到相似站模板直接复用，不从零写schema。每完成一站强制沉淀回skill。

### Step 0 — 查经验库（省时间关键，别跳）
```powershell
# spec/retail 各查各的 cases
Select-String -Path .kiro/skills/overseas-schema-gen/spec/cases.jsonl -Pattern "<域名特征|lineup|product-page>"
Select-String -Path .kiro/skills/overseas-schema-gen/retail/cases.jsonl -Pattern "<域名特征|pdp|search>"
```
读对应 `reference.md`（信号→解法+坑）与对应 `playbook/README.md`（结构型索引）。命中相似站 → 以它为起点改，通常 1 轮过。

**Agent探索决策树**（查到相似站后的动作）：
```
查cases.jsonl命中相似站?
  ├─ YES → 复用其adapter.py模板，改code/base_url/country/选择器 → 跳到Step 2
  ├─ NO  → 全新结构 → Step 1探测 → 查playbook匹配结构型 → Step 2
  └─ 被WAF封 → 标blocked，不建adapter，记入cases.jsonl
```

### Step 1 — 探测
```bash
# SPEC：探官网入口
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --entry "<入口URL>" --code <code> [--expect-models N]
# RETAIL：探搜索页 + 型号页
py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --site <code> --model "<真实型号>" --base-url "<站根>"
```
看四个信号：`best_link_selector`/`purity`、`load_more_hint`、`pager.has_numbered_pages`、`reviews.pagination_type`。查对应 playbook 套参数。

### Step 2 — 写 / 改 schema（草稿到 `data/schema_drafts/`）
严格按 `docs/AdapterSpec_schema规范.md`（SPEC）或 `docs/RetailSpec_schema规范.md`（RETAIL）。开工先复制 `task_template.md` 到 `data/onboard/<code>_task.md` 边做边填。

### Step 3 — 校验 + 实测（两层都过）
```bash
# SPEC
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --verify data/schema_drafts/x.spec.json --entry "<入口URL>"
# RETAIL 静态 + 真实站小样
py -3.12 -c "from overseas.retail_spec import load_retail_spec, validate_retail_spec; print(validate_retail_spec(load_retail_spec('data/schema_drafts/x.retail.json')))"
py -3.12 -m overseas.cli retail-health --spec ... --models-file ... --max-models 3
```
达标后独立 sandbox db 真实抓取一轮，不污染正式库：
```bash
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/x.spec.json
# 或 retail：py -3.12 scripts/mx_line.py retail --site <code> --models-file ... --spec ... --save-db
```

### Step 4 — 不达标就诊断（每轮只动一个变量）
换档 → 改选择器 → 加交互，**每轮只动一个变量**，否则分不清哪个改动起作用（§9.13 那次 4 小时排查的教训）。常见失败维度及对症详见对应分支 `reference.md`。

**判定"该手写适配器"（别硬凑，及时止损）**：
SPEC：型号 slug 无规律、Load More 通用点击点不开、需要带签名接口、多级页面才拿型号 → `verdict="needs_handwritten"`。
RETAIL：需登录/验证码/加购才显示价、评价只有草稿框（防爬假页面）→ `verdict="needs_human"`。

### Step 5 — 转正 + 强制沉淀（★ 每次任务必做，漏=白干）
1. 达标 → schema 转正：SPEC 到 `docs/specs/<code>.spec.json`、RETAIL 到 `docs/retail_specs/<code>.retail.json`，`status` 改 `approved`。adapter.py 放到 `overseas/sites/<code>/adapter.py`。
2. **追加 cases 一条**：SPEC → `spec/cases.jsonl` / RETAIL → `retail/cases.jsonl`（格式见文件头注释；标 `success` 仅限 SPEC 找全率 ≥95% / RETAIL 匹配准确率 100%；80~95% 标 `partial` 并说明；超边界标 `needs_handwritten`/`needs_human`；被拦标 `blocked`。**不要为好看标 success。**）
3. 有新规律/坑 → 更新对应 `reference.md`。
4. 全新结构型 → 在对应 `playbook/` 加套路 + 更新 `playbook/README.md`。
5. **跑一次自检**（两分支都跑）：
```bash
py -3.12 .kiro/skills/overseas-schema-gen/spec/selfcheck.py
py -3.12 .kiro/skills/overseas-schema-gen/retail/selfcheck.py
```

### Step 6 — 转交硬编码引擎（探索结束，进入运维）
schema + adapter 转正后，**探索工作结束**。后续抓取由硬编码引擎直接调用：

```bash
# SPEC站 → spec_crawl()  (overseas/scenarios.py:802)
python -m overseas.cli --db data/overseas.db spec-crawl --site <code>

# RETAIL站 → s2_monitor_known()  (overseas/scenarios.py:397)
python -m overseas.cli --db data/overseas.db monitor --site <code> --from-db --limit 10

# 网评 → s4_review_incremental()
python -m overseas.cli --db data/overseas.db reviews --site <code> --sku <sku> --pages 3

# 全自动 → LangGraph引擎
python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia
```

**引擎不复用探索逻辑**——adapter.py只负责URL规则+能力标记+选择器，
引擎通过`adapter.series_entries()`/`adapter.search_url()`/`adapter.product_url()`等接口驱动，
所有站点走同一条硬编码路径。

---

## 3. OPS 分支：已有适配器的运行与运维

> schema 转正后要跑全量/断点/监控/清理 → **统一走 ops**（内容即原 overseas-crawl-ops，已并入本 skill）。

用户说"跑全量 / 断点续跑 / 监控 / 僵尸 / 污染 / supervisor / rebuild" → 直接看 [ops/crawl-ops.md](ops/crawl-ops.md)。

### 3.1 运维要诀（浓缩）
- 长时全量**必须** `--save-db`、串行、先备份、supervisor 断点续抓。
- **僵尸检测**：进程活着 ≠ 在干活。1h 无新 DB 写入 + 日志尾 `socket.send() raised exception` = 卡死，杀子进程 MySQL no supervisor 自愈。这是上一轮 14h 空转的血泪教训。
- **污染修复**：先备份受影响行 → DELETE（仅 task='price' 且 price IS NOT NULL）→ `rebuild_amazon_csvs.py --region`。原则：缺价 > 错价。

详见 [ops/](ops/)。

---

## 4. 工具边界（哪个分支用什么，别混淆）

| 要做的事 | 用什么 | 不要用什么 |
|---|---|---|
| 探 SPA 入口页 DOM 结构 | `spec/probe.py --entry` (BrowserFetcher) | 通用 web fetch（拿不到 JS 渲染） |
| 探零售站搜索/PDP | `retail/probe.py --site --model` | 通用 web fetch |
| 校验 schema | `spec/probe.py --verify` / `validate_retail_spec` | 自我感觉 |
| 实际抓取 | `overse.cli spec-sync` / `mx_env.py retail --spec` | 自己写爬虫 |
| 长期运维 / 监控 / 自愈 | [ops/crawl-ops.md](ops/crawl-ops.md) (supervisor / watchdog) | 前台跑全量（会被超时 kill） |
| 全自动抓取编排 | `overseas/flow/` LangGraph引擎 | 手动逐站调用 |
| 调研报告生成 | `overseas/report/` DeepResearch 6-Agent | 人工写报告 |

---

## 5. Agent自动探索能力现状与规划

### 5.1 当前能力（已实现）

| 能力 | 状态 | 说明 |
|------|------|------|
| 经验库检索 | ✅ | cases.jsonl(548条) + reference.md(1200+行) + playbook |
| 探测工具 | ✅ | spec/probe.py + retail/probe.py (BrowserFetcher) |
| 模板复用 | ✅ | 260个adapter.py模板可复用，改code/base_url/选择器即可 |
| 校验工具 | ✅ | selfcheck.py + validate_spec + retail-health |
| 硬编码引擎 | ✅ | spec_crawl/s2_monitor_known/s4_review_incremental |
| 全自动抓取 | ✅ | LangGraph 7节点并行引擎 |
| 报告生成 | ✅ | DeepResearch 6-Agent防幻觉报告 |

### 5.2 Agent自动探索流程（当前半自动，目标全自动）

```
当前: 人工读skill → 人工探测 → 人工写adapter → 人工校验 → 人工转正
目标: Agent读skill → probe.py自动探测 → Agent匹配playbook → 
      Agent生成adapter → selfcheck自动校验 → 小样实测 → 自动转正
```

**Agent探索的关键决策点**（每个都查skill经验库）：
1. **是SPEC还是RETAIL？** → 看是品牌官网(有lineup)还是电商站(搜索/PDP)
2. **有相似站吗？** → 查cases.jsonl域名特征/结构特征
3. **用什么playbook？** → 查probe结果信号(container/pagination/reviews)匹配playbook
4. **adapter.py怎么写？** → 复用相似站模板，改code/base_url/country/选择器
5. **校验通过吗？** → selfcheck.py + 小样实测(含该站确实没有的型号)
6. **WAF封了吗？** → 被403/CF/DataDome封 → 标blocked，不建adapter

### 5.3 Agent探索的边界（什么时候该停）

| 信号 | 判定 | 动作 |
|------|------|------|
| 型号slug无规律 | `needs_handwritten` | 标cases，上报引擎缺口 |
| 需登录/验证码/加购才显示价 | `needs_human` | 标cases，需人工介入 |
| WAF硬封(403/CF/DataDome) | `blocked` | 标cases，需住宅IP |
| SPA软封(200但0产品) | `blocked` | 标cases，需逆向API或住宅IP |
| URL 404/400 | `url_wrong` | 人工查正确URL重探 |

---

## 5. 内部文件结构（新增/沉淀时只在这棵树下用）

```
overseas-schema-gen/
├── SKILL.md                    ← 本文件：入口 + 五选型 + 强制沉淀
├── spec/                       ← SPEC 分支（AdapterSpec 官网发现）
│   ├── SKILL.md                 分支自有完整流程 / 铁律 / 工具边界
│   ├── reference.md             判断经验库（信号→解法 + 已知坑）
│   ├── cases.jsonl              案例库（Step 0 检索相似站）
│   ├── playbook/                结构型套路（static_lineup / numbered_paginate / ...）
│   ├── task_template.md       新站任务卡模板
│   ├── probe.py / selfcheck.py
├── retail/                     ← 零售分支（RetailSpec 价/评）
│   ├── SKILL.md / reference.md / cases.jsonl
│   ├── playbook/              （single_shop / multi_shop / sitemap_discovery / ...）
│   ├── task_template.md / probe.py / selfcheck.py
├── ops/                        ← 运维分支
│   └── crawl-ops.md            （全量/自愈/监控/污染/LangGraph并行/区域分类/DeepResearch报告）
├── flow/                       ← LangGraph全自动抓取编排引擎
│   └── README.md               （7节点+并行+断点续跑+边界条件）
└── report/                     ← DeepResearch调研报告生成
    └── README.md               （6-Agent+防幻觉+信源追溯）
```

**参考文档**（不重复，用则读原文）：
- `docs/LLM自动适配器技术方案.md`（SPEC 全实测 + §9.10~§13 历史）
- `docs/零售线多国自动适配技术方案.md`（RETAIL 全局）
- `docs/AdapterSpec_schema规范.md`、`docs/RetailSpec_schema规范.md`

---

## 7. 经验驱动的自动探索闭环（核心设计）

```
         ┌─── skill经验库(活文档) ───────────────────────────┐
         │ cases.jsonl(548条) + reference.md(1245行)          │
         │ + playbook(结构型套路) + selfcheck.py              │
         └────────┬──────────────────────────┬───────────────┘
                  │ ①查相似站                 │ ⑥沉淀新经验
                  ▼                           ▲
  ②Agent探索      │                           │
  probe.py探测    │                           │
  → ③匹配playbook │                           │
  → ④生成adapter  │                           │
  → ⑤selfcheck   │                           │
  → 小样实测      │                           │
  → 转正──────────┘                           │
                  │                           │
                  ▼ 产出: schema.json+adapter.py│
  ┌──────────────────────────────────────────┐│
  │ 硬编码引擎(可复用，不需再探索)              ││
  │ spec_crawl()/s2_monitor_known()           ││
  └──────────┬───────────────────────────────┘│
             │ DB数据                          │
             ▼                                │
  ┌──────────────────────────────────────────┐│
  │ LangGraph全自动抓取 → DeepResearch报告    ││
  └──────────────────────────────────────────┘│
                                               │
  每完成一站 → ⑥沉淀回cases.jsonl ──────────────┘
  (越用越强: 新站查到相似站 → 1轮过)
```

**核心闭环**：
1. **探索前查** — Agent查cases.jsonl找相似站，复用模板不从零开始
2. **探索后沉淀** — 每完成一站追加case，下次相似站可直接复用
3. **探索结束转硬编码** — adapter.py转正后，引擎直接调用，不再探索
4. **skill越用越强** — 548条cases覆盖260站，新站命中率越来越高