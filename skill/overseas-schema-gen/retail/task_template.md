# 新站接入任务卡（RetailSpec 模板）

> 用法：接新站时复制本模板到 `data/onboard/<code>_task.md` 开始填。
> 目的是**让每一步的结论落在纸上**，避免调到后面忘了前面试过什么。
> 完成后按 §6 把内容压缩成一条写进 `cases.jsonl`。

---

## 基本信息

| 项 | 值 |
|---|---|
| `code` | |
| 站点 / 国家 | |
| base_url | |
| 探测用真实型号 | ⚠️ **必填**（在站上确认在售，如 75U6SV） |
| 搜索关键词形态 | 裸型号 or 品牌+型号 |
| 开始时间 | |

---

## Step 0 — 查经验

```powershell
Select-String -Path .kiro/skills/overseas-schema-gen/retail/cases.jsonl -Pattern "<域名或路径特征>"
```

| 项 | 记录 |
|---|---|
| 命中的相似站 | |

---

## Step 1 — 探搜索页

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --site <code> --model <真实型号> --base-url <URL> [--brand]
```

| 信号 | 值 |
|---|---|
| `best_candidate_selector` | |
| 候选数 / 纯度 | |
| 商品 URL 样本 | |
| 是否直达 PDP | |
| 评价翻页方式 | none / url_page / query_param / click_more |
| 多店铺 | single_shop / multi_shop |

---

## Step 2 — 写 schema

路径：`data/schema_drafts/<code>.retail.json`
参考：`docs/retail_specs/liverpool_mx.retail.json`（自营） / `kakaku_jp.retail.json`（聚合）

关键段记录（写完后核对）：

| 段 | 我填什么 | 为什么 |
|---|---|---|
| `search.container` | | |
| `product.size` | h1 兜底 + kv | 规格表 kv 不一定每页都在 |
| `price.*` | 选择器 + transform | 必要时 jsonld 兜底 |
| `reviews.container` | | |
| `match.reject.*` | soporte/accesorios 等 | 防配件 |
| `paginate.reviews.type` | | |
| `shops.mode` | | |

---

## Step 3 — 校验

```bash
py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --verify data/schema_drafts/<code>.retail.json
```

| 轮 | 校验结果 | 修复了什么 |
|---|---|---|
| 1 | | |
| 2 | | |

## Step 4 — 真实站小样

```bash
py -3.12 -m overseas.cli retail-health --spec data/schema_drafts/<code>.retail.json --models-file <型号清单> --max-models 3
```

| 指标 | 值 | 判定 |
|---|---|---|
| 选择器命中 | ✓/✗ | 合格 |
| 价格填充率 | | ≥95% |
| 评价可抽 | ✓/✗ | |

## Step 5 — 全量跑 + 落库导出

```bash
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 scripts/mx_line.py retail --site <code> --models-file <型号全集> --spec <draft> --save-db
```

| 结果 | 值 |
|---|---|
| 匹配 N 个 / 全部型号 | |
| 价格填充 | |
| 评价条数 | |
| 抽检误匹配 | 0 / N |

---

## Step 6 — 沉淀

1. schema 转正：`docs/retail_specs/<code>.retail.json`
2. `cases.jsonl` 追加一条（verdict/signals/solution/result/lesson）
3. 新规律 → `reference.md`
4. 新结构型 → `playbook/` 新增 + 更新索引
5. `selfcheck.py` 跑一遍

**验收红线**：`on_no_match` = skip；匹配准确率抽检 = 100%；价格填充率 ≥95%。