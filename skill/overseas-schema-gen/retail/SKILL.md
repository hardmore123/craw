---
name: overseas-schema-gen-retail
description: 给一个陌生零售站（电商价格+网评），用已知型号探结构，产出可直接执行的 RetailSpec（六段 + match/paginate/incremental/shops），交给 mx_retail / crawl 引擎抓该站全部型号的价格与网评。当用户要求「接入新的零售站」「生成某零售站的 schema / 适配配置」「诊断某站为什么匹配不到型号 / 价格抓不到」时使用。
---

# 零售站 RetailSpec 生成

给一个陌生电商零售站（Liverpool / Coppel / Palacio / Amazon / BestBuy ...），
用**型号驱动的探测**产出一份经真实页面验证的 `RetailSpec`，交给硬编码引擎
（`crawl_model_on_site` / `crawl_region_retail`）抓该站价格与网评。

**核心纪律：你是判断力，不是执行器。** 探测/校验/抓取都有现成程序（本项目
P0~P2 已落地），你的价值在"看信号 → 选套路 → 定参数 → 看失败原因 → 调整"。
不要重写探测逻辑，不要手工数 HTML。

---

## 铁律（违反即返工）

1. **先读 `robots.txt`，再写任何选择器。** 站点禁抓搜索路径却放行商品页时，改走
   `playbook/sitemap_discovery.md`，不要硬用搜索。**robots 结论必须抄进交付文档。**
   "先抓了再说"不是选项。
2. **只产配置，不产代码。** `match.on_no_match` 必须 `skip`（宁缺毋滥）；分页/交互只声明
   `paginate.type`（none / url_page / query_param / click_more），不写任意 JS。
3. **不越过引擎能力边界。** 枚举值必须在 `overseas/retail_spec.py` 白名单内。
   写引擎不认的值 = 静默失效。**需要越界 → 记为引擎缺口上报，不要伪造配置糊过去。**
4. **校验不可跳过。** 哪怕很确信也必须跑 `validate_retail_spec` + `retail-health` 小样实测。
   "我看着对"不算通过，真实页面的匹配/填充率说了算。
5. **`no_item` 与 `failed` 严格分开。** 搜索页没渲染/被拦/出口 IP 不对 = `failed`；
   只有"站上确实没有"才是 `no_item`。分错等于静默丢数据。
6. **强制收尾沉淀。** 每次任务结束（成功或失败）都必须追加 `cases.jsonl` 一条 +
   必要时更新 `reference.md`。这是 skill 会越用越强的唯一机制。

---

## 主流程

### Step 0 — 先查 robots 与经验（省时间的关键，别跳）

**0a) 读 `robots.txt`（合规闸，早于一切技术判断）**

看两件事：**搜索路径是否被禁**、**商品页是否被放行**、**有没有 `Sitemap:` 行**。

```
Disallow: /en-ca/search          ← 搜索被禁
Allow:    /en-ca/product/        ← 商品页放行
Sitemap:  https://.../sitemap_index.xml
```

三个信号同时出现 → 直接走 `playbook/sitemap_discovery.md`，别浪费时间探搜索页。
（bestbuy.ca / bestbuy.com 都是这个形态。）

**0b) 检索案例库**

新站十有八九和做过的站同型：

```powershell
# 按域名/建站特征/URL 路径特征匹配相似站
Select-String -Path .kiro/skills/overseas-schema-gen/retail/cases.jsonl -Pattern "self|/pdp/|elastic|search"
```

读 `reference.md`（信号→解法规律 + 已知坑）与 `playbook/README.md`（结构型索引）。
命中相似站 → 直接以那份 RetailSpec 为起点改，通常 1 轮就过。

### Step 1 — 探搜索页（认识这个站怎么搜型号）

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/retail/probe.py --site liverpool_mx --model "75U6SV" --base-url "https://www.xxx.com"
```

`--model` 用一个**真实存在**的型号（先问她/在站上搜确认），探测报告核心信号：

| 信号 | 怎么用 |
|---|---|
| `search.best_candidate_selector` + `purity` | 搜索候选容器。**看 `purity`（含型号卡片占比）而非命中数** |
| `search.sample_pdp_hrefs` | 商品 URL 形态，推 `search.container` / `product.url` |
| `product_page.h1` + `model_in_h1` | PDP 标题走「h1 + size 兜底」 |
| `reviews.pagination_type` | 评价翻页：`none`=评价在商品页 / `url_page`=独立评价页页码 URL / `query_param`=?page= / `click_more`=点加载更多 |
| `shops.mode` | 是否聚合站（多店铺报价表）→ `multi_shop` / `single_shop` |

据此填 `search` / `match` / `price` / `reviews` / `paginate` / `shops` 段。查 `playbook/` 对应结构型。

### Step 2 — 写 / 改 schema

草稿落到 `data/schema_drafts/<code>.retail.json`。严格按
`docs/RetailSpec_schema规范.md` 写。参考已验证站：`docs/retail_specs/liverpool_mx.retail.json`
（自营 single_shop）、`docs/retail_specs/kakaku_jp.retail.json`（聚合 multi_shop）。

> 建议开工时先复制 `task_template.md` 到 `data/onboard/<code>_task.md` 边做边填。

### Step 3 — 校验 + 实测（两层都要过）

```powershell
# 1) 静态校验
py -3.12 -c "from overseas.retail_spec import load_retail_spec, validate_retail_spec; print(validate_retail_spec(load_retail_spec('data/schema_drafts/x.retail.json')))"
# 2) 真实站小样实测（health 探测：抽 3 个真实型号验证选择器命中 + 价格填充率）
py -3.12 -m overseas.cli retail-health --spec data/schema_drafts/x.retail.json --models-file <型号清单> --max-models 3
```

达标后跑一次真实抓取确认（独立 sandbox db，别污染正式库）：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 scripts/mx_line.py retail --site <code> --models-file <型号> --spec data/schema_drafts/<code>.retail.json --save-db
```

### Step 4 — 不达标就诊断

按失败维度对症（`reference.md` 有排查经验）：

| 失败 | 先怀疑 |
|---|---|
| `search.container` 未命中 | 选择器错；页面懒加载（需 `fetch.requires_browser`）；搜索直达 PDP（单结果 302） |
| 价格抽不到 | `price` 选择器错；页面走 JSON-LD（加 `jsonld: "offers.price"` 兜底） |
| 型号匹配不上 | `match.verify.require_model_in` 字段错；候选标题形状与 `sku` 拼合不理想 |
| **主力系列整批 `no_item`** | **`min_core_len` 太高**（主干 ≤3 位的型号被整条跳过）；见 `reference.md` §11.4 |
| **候选只剩 1 条** | **`search.sku` 配了整页 `regex`**（每张卡片解析成同一 SKU）；见 `reference.md` §11.6 |
| **搜索关键词里出现中文** | 型号清单 `brand_name`/`search_query` 带国别后缀；见 `reference.md` §11.7 |
| **200 但页面不是目标内容** | 国际选择页/地区插屏；**按标题判，不要按状态码**；见 `reference.md` §11.2 |
| **价格记到别的型号头上** | PDP 权威型号校验没跑，或旧 SKU 301 到了无关商品；见 `reference.md` §11.5 |
| 评价 0 条 | 评价懒加载（加大 `fetch` 的滚动/等待）；container 选择器错；**或 `reviews.source` 填错** |
| 被拦 | 站点防护等级设高（`protection: L3`）或用代理；**先区分是站点拦还是本地网关拦**（§11.3） |

**换档 → 改选择器 → 加兜底，每轮只动一个变量**，否则分不清哪个改动起作用。

**判定"该人工写适配器"**（别硬凑，及时止损）：需要登录/验证码、价格在加购后才显示、
评价只有草稿框（防爬假页面）。→ `cases.jsonl` 标 `verdict: "needs_human"` 并写原因。

### Step 5 — 收尾沉淀（强制）

1. 达标 → schema 转正到 `docs/retail_specs/<code>.retail.json`
2. **追加 `cases.jsonl` 一条**（格式见该文件头注释）
3. 有新规律/坑 → 更新 `reference.md`
4. 遇到全新结构型 → `playbook/` 加一份套路 + 更新 `playbook/README.md`
5. 撞引擎边界 → 明确写"缺哪个档位"，这是 P0 排期输入
6. **跑一次骨架自检**：

```powershell
py -3.12 .kiro/skills/overseas-schema-gen/retail/selfcheck.py
```

---

## 工具边界

| 要做的事 | 用什么 | 不要用什么 |
|---|---|---|
| 抓渲染后的 SPA / AJAX 页面 | `probe.py`（内部走 BrowserFetcher） | 通用 web fetch |
| 判断评价翻页方式 | `probe.py` 报告的 `reviews.pagination_type` | 肉眼看 URL 猜 |
| 校验 schema | `validate_retail_spec` | 自我感觉 |
| 实际抓取 | `mx_line.py retail --spec` | 自己写爬虫 |
| 定期探测是否还健康 | `retail-health` | 手动重抓 |

## 参考

| 文件 | 用途 |
|---|---|
| `reference.md` | 结合零售站实测的判断经验（选择器/兜底/坑） |
| `cases.jsonl` | 案例库，Step 0 检索 |
| `playbook/README.md` | 结构型套路索引（含 robots 合规决策路径） |
| `playbook/sitemap_discovery.md` | **站点禁抓搜索时的确定性发现套路**（bestbuy.ca 实测） |
| `task_template.md` | 新站接入任务卡模板 |
| `probe.py` | 探测入口（内部调 `overseas.retail_probe`） |
| `selfcheck.py` | 骨架自检（文件完整性 + 案例可解析 + 规范同步） |

项目文档：`docs/RetailSpec_schema规范.md`（契约）、`docs/零售线效果指标与验收标准.md`（指标）、
`docs/零售线多国自动适配技术方案.md`（方案）。