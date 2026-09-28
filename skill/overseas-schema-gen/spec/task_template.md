# 新站接入任务卡（模板）

> 用法：接新站时复制本模板到 `data/onboard/<code>_task.md` 开始填。
> 目的是**让每一步的结论落在纸上**，避免调到后面忘了前面试过什么。
> 完成后按 §6 把内容压缩成一条写进 `cases.jsonl`。

---

## 基本信息

| 项 | 值 |
|---|---|
| `code` | |
| 品牌 / 地区 | |
| 入口 URL | |
| **官网标称型号数** | ⚠️ **必填**，去官网数一遍。不填等于关掉找全率闸门 |
| 官网标称系列数 | |
| 开始时间 | |

---

## Step 0 — 查经验

```powershell
Select-String -Path .kiro/skills/overseas-schema-gen/spec/cases.jsonl -Pattern "<域名或路径特征>"
```

| 项 | 记录 |
|---|---|
| 命中的相似站 | |
| 可复用的 schema 起点 | |
| `reference.md` 相关条目 | |
| `playbook` 预判结构型 | |

> 命中相似站就从那份 schema 改起，通常 1 轮就过。没命中再从 Step 1 从头探。

---

## Step 1 — 探入口页

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --entry "<URL>" --code <code> --expect-models <N>
```

| 信号 | 值 | 我的判断 |
|---|---|---|
| `best_link_selector` | | |
| 该选择器命中数 / 纯度 | | |
| 次优候选（对比用） | | |
| `product_url_pattern` | | |
| `sample_product_hrefs`（贴 2~3 个） | | |
| `load_more_hint` | | |
| `pager.has_numbered_pages` | | |
| `pager.has_next_button` | | 若无编号页码 → 判为轮播，忽略 |
| `link_growth` | | |

**结论**：结构型 = ______，套用 `playbook/______.md`

---

## Step 2 — 探型号页

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --model "<型号页URL>" --code <code> --save-html
```

| 信号 | 值 | 判断 |
|---|---|---|
| `embedded_json_markers` | | |
| `generic_spec_js_rows` | | ≥8 则通用抽取够用 |
| `spec_container_candidates` Top3 | | 用于定 `page_wait` |

**结论**：`extract_order` = ______

若走 `embedded_json`，核对真实键名（从 `--save-html` 存下的 HTML 里搜）：

| 配置字段 | 该站真实键名 |
|---|---|
| `root_marker` | |
| `chapters_field` | |
| `items_field` | |
| `item_name_field` | |
| `values_field` | |
| `value_name_field` | |

---

## Step 3 — 写 schema

草稿路径：`data/schema_drafts/<code>.spec.json`

**自查清单**（写完逐项打勾）：

- [ ] `expected.model_count` 已填真实数字
- [ ] `discover.mode` = `dom_anchor`（其它值引擎不认）
- [ ] `model_url_regex` 含至少 1 个捕获组
- [ ] `entry_wait` 填的是产品链接选择器（不是 `body`）
- [ ] `extract_order` 最后一位是 `generic_spec_js`（兜底）
- [ ] 静态站**没有**写 `discover.paginate`
- [ ] `snippet_ref`（若有）在 `numbered_pages_generic` / `philips_paginate` 之内
- [ ] 没有编造引擎不认的字段

---

## Step 4 — 验证

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/spec/probe.py --verify data/schema_drafts/<code>.spec.json
```

| 轮次 | 静态校验 | 实测 score | 发现型号 | 找全率 | 失败原因 | 本轮只改了什么 |
|---|---|---|---|---|---|---|
| 1 | | | | | | — |
| 2 | | | | | | |
| 3 | | | | | | |

> **纪律：每轮只动一个变量。** "本轮只改了什么"这一列如果填了两项以上，
> 说明违反纪律，结果不可归因。

真实抓取确认（分页站必做，因为 `--verify` 用缓存 HTML 会低估找全率）：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/<code>.spec.json
```

| 项 | 结果 |
|---|---|
| 实抓系列数 / 型号数 | |
| 对比官网标称 → 找全率 | |
| 守恒（发现数 == 落库数） | |
| 抽 3 型号的规格行数 | |

---

## Step 5 — 诊断记录（不达标时填）

| 症状 | 怀疑原因 | 验证方式 | 决定性证据 | 结论 |
|---|---|---|---|---|
| | | | | |

> 要找**能排除其它可能**的证据，不是"符合猜想"的证据。
> 历史教训：一次排查初判是"分页翻不到尽头"，证据（首屏 122 链接去重后 12 个型号、
> 两种正则都是 12）推翻了它，真实根因是 JS 片段的正则转义 bug。

**止损检查**（命中任一即停，转 `playbook/needs_handwritten.md`）：

- [ ] 型号 slug 无规律，正则框不住
- [ ] Load More 通用点击点不开
- [ ] 需多级页面才拿到型号（引擎缺口 P0-1）
- [ ] 数据来自带签名接口（引擎缺口 P0-2）
- [ ] 规格需点 tab 展开（无此解析档）
- [ ] 强反爬 / 验证码

---

## Step 6 — 收尾沉淀（强制）

- [ ] schema 转正到 `docs/specs/<code>.spec.json`，`status` 改 `approved`
- [ ] **追加 `cases.jsonl` 一条**（成功失败都要）
- [ ] 有新规律/新坑 → 更新 `reference.md`，注明来源站点
- [ ] 全新结构型 → 补 `playbook/` 并更新其 `README.md` 索引
- [ ] 撞到引擎边界 → `gap` 字段写清缺哪个档位
- [ ] 跑 §回归基线确认没破坏已有站点

### 本次要写进 cases.jsonl 的内容

```json
{"code":"","brand":"","entry_url":"","date":"","verdict":"",
 "signals":{},
 "solution":{},
 "result":{"series":0,"models":0,"expected_models":0,"coverage":""},
 "lesson":"",
 "playbook":""}
```

### 一句话教训（最有价值的产出）

> 写下这次学到的、下次遇到同类站能省时间的那一句：
>
> ______________________________________________
