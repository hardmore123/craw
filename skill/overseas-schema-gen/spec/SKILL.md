---
name: overseas-schema-gen-spec
description: 给一个电视品牌官网入口 URL，探测站点结构并产出可直接执行的 AdapterSpec schema，用于抓取该站全部电视型号规格。当用户要求「接入新的电视站点」「生成某站的 schema / 适配器配置」「诊断某站为什么抓不全型号」时使用。
---

# 电视站 SPEC schema 生成

给一个陌生电视品牌官网 URL，产出一份经真实页面验证的 `AdapterSpec`，交给
`generic_spec` 引擎抓全站型号规格。

**核心纪律：你是判断力，不是执行器。** 探测/校验/抓取都有现成程序，你的价值在
"看信号 → 选套路 → 定参数 → 看失败原因 → 调整"。不要重写探测逻辑，不要手工数 HTML。

---

## 铁律（违反即返工）

1. **只产配置，不产代码。** 需要执行 JS 只能 `snippet_ref` 引用已登记片段，禁止内联 JS。
2. **不越过引擎能力边界。** schema 里的枚举值必须在白名单内，见
   `docs/AdapterSpec_schema规范.md` §6。写了引擎不认的值 = 静默失效或直接报错。
   **发现需要越界 → 记为引擎缺口上报，不要伪造配置糊过去。**
3. **校验不可跳过。** 哪怕你很确信，也必须跑 `validate_spec` + `score_spec_on_html`。
   "我看着对"不算通过，找全率说了才算。
4. **强制收尾沉淀。** 每次任务结束（成功或失败）都必须追加 `cases.jsonl` 一条 +
   必要时更新 `reference.md`。**这是 skill 会越用越强的唯一机制，漏了等于白干一次。**

---

## 主流程

### Step 0 — 先查经验（省时间的关键，别跳）

新站十有八九和做过的站同型。先检索案例库：

```powershell
# 按域名/建站特征/URL 路径特征匹配相似站
Select-String -Path .kiro/skills/overseas-schema-gen/spec/cases.jsonl -Pattern "lineup|product-page|c-p"
```

读 `reference.md`（信号→解法规律 + 已知坑）与 `playbook/README.md`（结构型索引）。
命中相似站 → 直接以那份 schema 为起点改，通常 1 轮就过。

### Step 1 — 探入口页

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --entry "<入口URL>" --code <code> [--expect-models N]
```

拿到探测报告，重点看四个信号：

| 信号 | 怎么用 |
|---|---|
| `best_link_selector` + `link_selector_ranking` | 产品链接选择器候选。**看 `model_like_ratio`（纯度）而不是只看命中数** |
| `product_url_pattern` + `sample_product_hrefs` | 推 `model_url_regex` |
| `load_more_hint` | `none`=静态列全 / `scroll`=懒加载 / `click_more_or_paginate` / `paginate_replace` |
| `pager.has_numbered_pages` | **只有编号页码是可靠分页信号**；孤立 next 按钮多半是轮播，忽略 |

据此填 `discover` 段。查 `playbook/` 对应结构型直接套参数。

### Step 2 — 探型号页

从 `sample_product_hrefs` 挑一个真实型号页：

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --model "<型号页URL>"
```

看规格载体判断 `extract_order`：

- 有内嵌 JSON（`"specification":{...}` 之类）→ `["embedded_json", "generic_spec_js"]`，
  并按真实键名填 `spec.embedded_json`
- 只有 DOM 表格 / dl / 键值对 → `["generic_spec_js"]`

### Step 3 — 写 schema

严格按 `docs/AdapterSpec_schema规范.md` 写。草稿落到：

```
data/schema_drafts/<code>.spec.json
```

> 建议开工时先复制 `task_template.md` 到 `data/onboard/<code>_task.md`，
> 边做边填。它把每一步的信号和判断落在纸上，避免调到后面忘了前面试过什么，
> 也直接产出 Step 6 要写进 `cases.jsonl` 的内容。

`expected.model_count` **务必填**（去官网数一遍）。不填等于关掉找全率闸门，
这是"抓一半当成功"最常见的原因。

### Step 4 — 验证

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --verify data/schema_drafts/<code>.spec.json --entry "<入口URL>"
```

两层都要过：静态校验 `problems` 为空，实测打分 `passed=true`。

达标后跑一次真实抓取确认规格抽得出来（用独立 sandbox db，别污染正式库）：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/<code>.spec.json
```

### Step 5 — 不达标就诊断

按失败维度对症，`reference.md` 有排查经验：

| 失败 | 先怀疑 |
|---|---|
| `link_selector` 未命中 | 选错选择器；或页面需要更长 `entry_extra_wait_ms` 才渲染 |
| 型号提不到 | `model_url_regex` 捕获组位置错；型号 slug 不规整 |
| 找全率低 | 分页/懒加载没配；或选择器只覆盖首屏 |
| 发现数过多 | 选择器太宽泛混入导航；开 `non_tv_filter` |
| 规格 0 行 | `extract_order` 选错档；`embedded_json` 键名不对 |

**换档 → 改选择器 → 加交互，每轮只动一个变量**，否则分不清哪个改动起作用
（这是 §9.13 那次 4 小时排查的教训）。

**判定"该手写适配器"的信号**（别硬凑，及时止损）：
型号 slug 无规律无法用正则框住、Load More 通用点击点不开、需要多级页面才拿到型号、
需要调用带签名的接口。→ 记入 `cases.jsonl` 标 `verdict: "needs_handwritten"` 并说明缺口。

### Step 6 — 收尾沉淀（强制）

1. 达标 → schema 转正到 `docs/specs/<code>.spec.json`，`status` 改 `approved`
2. **追加 `cases.jsonl` 一条**（格式见该文件头部注释）
3. 有新规律/新坑 → 更新 `reference.md`
4. 遇到全新结构型 → 在 `playbook/` 加一份套路，并更新 `playbook/README.md` 索引
5. 撞到引擎能力边界 → 明确写出"缺哪个档位"，这是 P0 排期的输入
6. **跑一次骨架自检**，确认 cases/文档没写坏：

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/selfcheck.py
```

自检会查：文件完整性、cases.jsonl 字段与**找全率和 verdict 是否自相矛盾**
（标 success 但 < 95% 会被拦）、文档死链、**引擎白名单与 schema 规范是否脱节**、
规范文档示例是否真能过校验、已转正 schema 是否仍合法。

> `verdict` 取值纪律：`success` 仅限找全率 ≥ 95%；80~95% 用 `partial` 并说明差额；
> 超边界用 `needs_handwritten`；被反爬拦住用 `blocked`。**不要为了好看标 success。**

---

## 工具边界

| 要做的事 | 用什么 | 不要用什么 |
|---|---|---|
| 抓渲染后的 SPA 页面 | `probe.py`（内部走 BrowserFetcher） | 通用 web fetch，拿不到 JS 渲染结果 |
| 判断分页方式 | `probe.py --entry` 的 `load_more_hint` | 自己肉眼猜 |
| 校验 schema | `probe.py --verify` | 自我感觉 |
| 实际抓取 | `overseas.cli spec-crawl --schema` | 自己写抓取脚本 |

---

## 参考

skill 内：

| 文件 | 用途 |
|---|---|
| `reference.md` | 判断经验库（信号→解法规律 + 已知坑） |
| `cases.jsonl` | 案例库，Step 0 检索相似站 |
| `playbook/README.md` | 结构型套路索引与决策路径 |
| `task_template.md` | 新站接入任务卡模板 |
| `probe.py` | 探测与校验脚本 |
| `selfcheck.py` | 骨架健康自检 |

项目文档：

| 文件 | 用途 |
|---|---|
| `docs/AdapterSpec_schema规范.md` | **schema 字段契约与引擎能力边界** |
| `docs/任务表与验收标准.md` | 引擎缺口的排期与验收定义 |
| `docs/现阶段技术方案与效果指标.md` | 指标体系与当前基线 |
| `docs/LLM自动适配器技术方案.md` | 历史全过程逐轮实测记录 |
