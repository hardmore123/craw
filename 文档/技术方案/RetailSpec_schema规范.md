# RetailSpec schema 规范（零售线：网评 + 价格）

> 版本：**v0.1（草案，供评审）**
> 适用：零售站的**价格监控**与**网评采集**两条线，覆盖多国多渠道。
> 与 SPEC 线的 `AdapterSpec` 是**两套独立 schema**，互不复用（原因见 §0.2）。
> 关联：`AdapterSpec_schema规范.md`（SPEC 线契约）、
> `零售线多国自动适配技术方案.md`（本 schema 的使用场景）

---

## 0. 定位与边界

### 0.1 这份 schema 管什么

```
型号清单 ──► 站内定位商品页 ──► 抽价格 ──► 抽评价（翻页+增量） ──► 导出
              ↑ discover/match      ↑ price    ↑ reviews/paginate
              └────────── RetailSpec 约束这一整条 ──────────┘
```

### 0.2 为什么不复用 AdapterSpec

基于代码事实的三点结构性差异：

| 维度 | SPEC 线（AdapterSpec） | 零售线（RetailSpec） |
|---|---|---|
| **入口** | 品牌官网 lineup 页，**发现型号**是目标 | 型号清单**已给定**，目标是"在这个站找到这个型号的页" |
| **核心风险** | 找不全型号（找全率） | **找错型号**（张冠李戴，把配件/无关商品当成目标） |
| **产出** | 一张规格表 | 价格快照（时序）+ 评价列表（增量累积） |
| **翻页语义** | 翻完为止（一次性全量） | 评价翻页要**增量停止**（遇到已知评价即停） |
| **多值** | 一个型号一套规格 | 一个型号**多个店铺多个价格**（kakaku 型） |

`AdapterSpec.discover` 的语义是"发现型号"，零售线不需要；
零售线需要的 `match`（型号匹配）、`incremental`（增量停止）、`shops`（多店铺）
在 AdapterSpec 里完全没有对应概念。强行合并会让两边都变复杂。

### 0.3 与存量 schema.json 的关系 ★

**RetailSpec 不是新造的，是对现有 `overseas/sites/<code>/schema.json` 的规范化 + 扩展。**

现有格式（以 `sites/amazon_mx/schema.json` 为成熟范例）已有六段：
`anchor` / `search` / `product` / `price` / `specs` / `summary` / `reviews`，
并支持 `selectors` 顺序回退、`transform` 管道、`kv` 键值提取、`media` 图片视频规则。
**这部分保持不变、完全兼容**，由 `Extractor` + `load_schema()` 执行。

本规范做三件事：
1. **补齐缺失段**：`match`（型号匹配）、`paginate`（评价翻页）、`incremental`（增量停止）、
   `shops`（多店铺价格）、`export`（导出映射）。
2. **把硬编码收进配置**：现状 kakaku 的多店铺、翻页、发售日全在
   `sites/kakaku_jp/adapter.py` 与 `scripts/crawl_kakaku_*.py` 里硬编码
   （其 schema.json 自己注释说"评价抓取主要由脚本用适配器方法完成"），
   跨国无法复用。本规范把这些能力声明化。
3. **解决店铺列跨国死结**（见 §6）。

---

## 1. 顶层结构

```jsonc
{
  "retail_spec_version": "0.1",
  "code": "liverpool_mx",
  "name": "Liverpool（墨西哥）",
  "country": "Mexico",          // 导出「所属国家」列
  "channel": "Liverpool",       // 导出「渠道」列
  "region": "mx",
  "base_url": "https://www.liverpool.com.mx",
  "currency": "MXN",            // 默认币种，价格抽取可覆盖
  "capabilities": ["price", "reviews"],   // 该站支持哪条线
  "generated_by": "skill:retail-schema-gen/2026-09-16",
  "status": "candidate",

  "anchor": "...",              // ← 以下为存量六段，格式不变
  "search": { ... },
  "product": { ... },
  "price": { ... },
  "specs": [ ... ],
  "summary": { ... },
  "reviews": { ... },

  "match": { ... },             // ← 以下为本规范新增段
  "paginate": { ... },
  "incremental": { ... },
  "shops": { ... },
  "export": { ... },
  "fetch": { ... },
  "expected": { ... }
}
```

---

## 2. 存量六段（格式不变，简述）

由 `overseas/extract.py` 的 `Extractor` 执行。字段规则形态：

```jsonc
{"selectors": ["#productTitle", "h1.title"], "transform": "clean_text"}
{"selectors": ["#acrPopover"], "attr": "title", "transform": "rating"}
{"self_attr": "id"}                                    // 容器元素自身属性
{"kv": {"container": "#detailBullets li", "key_contains": "modelo", "value": "td"}}
{"regex": "\"asin\"\\s*:\\s*\"([A-Z0-9]{10})\""}       // 兜底：正则
{"jsonld": "offers.price"}                             // 兜底：结构化数据
```

**多选择器兜底链是刚需，不是过度设计**——实测同一站不同模板下选择器存在性不同。

| 段 | 作用 | 关键字段 |
|---|---|---|
| `anchor` | 页面有效性锚点 | 单个选择器字符串 |
| `search` | 搜索结果页取候选商品 | `container` / `sku` / `title` / `url` / `url_template` |
| `product` | 商品主体 | `title` / `brand` / `model` / `category` / `size` |
| `price` | 价格快照 | `price` / `currency` / `list_price` / `in_stock` / `raw_text` |
| `specs` | 规格（数组，多策略） | `type: table\|kvlist` / `container` / `key` / `value` / `skip_keys` |
| `summary` | 评分聚合 | `avg_rating` / `total_count` |
| `reviews` | 评价列表 | `container` / `limit` / `fields{...}` / `media{images,videos}` |

`reviews.fields` 必须含 `review_key`（增量去重的唯一键，见 §5）。

---

## 3. `match` — 型号匹配（零售线最高风险项）★

**这是零售线的红线段。** 型号匹配错了会导致"把 55 寸的价格记到 65 寸头上"或
"把电视支架的评价当成电视评价"，比抓不到更糟。

```jsonc
"match": {
  "strategy": "search_then_verify",   // search_then_verify | url_template | sku_map
  "search_keyword_template": "{brand} {model}",
  "candidate_limit": 40,

  "verify": {
    "require_model_in": ["sku", "title"],   // 型号必须出现在这些字段里
    "normalize": "upper_alnum",             // 归一化后比对（去空格/连字符）
    "allow_core_match": true,               // 允许主干匹配（55QD8SF-PRO → QD8SF）
    "core_strip_leading_size": true,        // 去前导尺寸
    "core_strip_suffix": ["pro", "max"],
    "min_core_len": 4
  },

  "reject": {
    "title_contains_any": ["soporte", "montaje", "control remoto", "funda"],
    "category_not_in": [],
    "url_contains_any": ["/accesorios/"]
  },

  "on_no_match": "skip"        // skip（宁缺毋滥，默认）| first（取第一个，不推荐）
}
```

| 字段 | 说明 |
|---|---|
| `strategy` | `search_then_verify`=站内搜索后校验（多数站）；`url_template`=型号可直接拼 URL；`sku_map`=需外部映射表 |
| `verify.require_model_in` | 型号归一化后**必须**出现在候选的这些字段里，否则不算命中 |
| `verify.allow_core_match` | 精确不中时允许主干匹配（`SiteAdapter.find_product` 已有此逻辑） |
| `reject.*` | 明确排除的品类词/路径，防配件混入 |
| `on_no_match` | ★**默认必须 `skip`**。基类 `find_product` 的注释明确："都不含型号 → 视为未匹配，返回 None（不再盲取第一个）" |

> **禁止把 `on_no_match` 设成 `first`** 来提高"覆盖率"。那是用数据污染换指标。

---

## 4. `paginate` — 评价翻页

```jsonc
"paginate": {
  "reviews": {
    "type": "url_page",          // url_page | query_param | click_more | none
    "url_template": "https://review.kakaku.com/review/{item_id}/Page={page}/",
    "param": "pageNumber",       // type=query_param 时
    "start": 1,
    "step": 1,
    "max_pages": 20
  }
}
```

| type | 说明 | 实例 |
|---|---|---|
| `url_page` | 页号嵌在路径里 | kakaku `.../Page=2/` |
| `query_param` | 页号在查询串 | `?pageNumber=2` |
| `click_more` | 点击加载更多（需浏览器） | 部分 SPA 站 |
| `none` | 评价只在商品页，不翻页 | 场景层会自动改用详情页评价 |

> `type=none` 时对应 `SiteAdapter.reviews_url()` 返回空串的既有约定，
> 场景层 `s4_reviews` 会回退用商品页评价。

---

## 5. `incremental` — 增量停止（省请求的关键）

评价是**累积型**数据，每周重跑不该全量重抓。

```jsonc
"incremental": {
  "key_field": "review_key",     // reviews.fields 里作唯一键的字段
  "stop_when": "page_all_known", // page_all_known（默认）| n_pages_no_new
  "no_new_rounds": 1
}
```

引擎行为（`scenarios.py` 的 `s4_reviews` 已实现此逻辑）：
读取该商品已入库的 `review_key` 集合 → 逐页抓 → **本页全为已知评价即停止翻页**。

| `stop_when` | 语义 |
|---|---|
| `page_all_known` | 本页无任何新 key 就停（默认，最省） |
| `n_pages_no_new` | 连续 N 页无新增才停（评价排序不稳的站用） |

> `review_key` 的选择很关键：必须是站点稳定的评价 ID（如 Amazon 的
> `data-hook='review'` 元素 id、kakaku 的 `ReviewCD`）。用"标题+作者"拼的伪 key
> 会因排版变化导致重复入库。

---

## 6. `shops` — 多店铺价格（跨国死结的解法）★

### 6.1 问题

现状 `overseas/price_export.py` 的价格表结构是：

```
FIXED_HEAD + SHOP_COLUMN_NAMES(8 个日本店铺) + FIXED_TAIL
```

那 8 个店铺列（ヨドバシ.com、エディオンネットショップ 等）是**日本 kakaku 专属硬编码**，
且 `KakakuJpAdapter.map_shop_columns()` 负责把店铺名映射到这 8 列。
墨西哥/加拿大不存在这些店铺，**直接复用会产出 8 列全空的无意义表**。

### 6.2 解法：店铺列由 spec 声明，导出动态生成

```jsonc
"shops": {
  "mode": "multi_shop",        // multi_shop（聚合站）| single_shop（自营站）
  "list": {
    "container": ".p-priceTable_row",
    "shop_name": ".p-priceTable_shop",
    "price": ".p-priceTable_price",
    "limit": 30
  },
  "columns": [                  // ★导出时的店铺列，按国家声明
    {"key": "ヨドバシ.com", "display": "友都八喜(Yodobashi.com)", "match": ["ヨドバシ"]},
    {"key": "エディオン", "display": "爱电王(EDION)", "match": ["エディオン"]}
  ],
  "lowest": {"price": ".p-lowest_price", "shop": ".p-lowest_shop"}
}
```

| 字段 | 说明 |
|---|---|
| `mode` | `multi_shop`=一个商品页列多家店铺报价（kakaku 型）；`single_shop`=自营站只有一个价（Amazon/Liverpool 型） |
| `list.*` | 店铺报价表的抽取规则 |
| `columns[]` | ★**导出列声明**。`key`=内部数据键，`display`=中文表头，`match`=店铺名匹配关键词（模糊匹配用） |
| `lowest` | 最低价与对应店铺 |

`mode=single_shop` 时只需 `price` 段，`shops` 可省略——导出走"渠道即店铺"的简化列。

> **改造要求**：`price_export.py` 需从"读模块级 `SHOP_COLUMN_NAMES` 常量"改为
> "读 spec 的 `shops.columns`"。这是任务表 P0-3 的内容。

---

## 7. `export` — 导出映射

网评走**通用 20 列模板**（`review_export.COLUMNS`，已跨站统一，不需按站配置）：

```
所属国家 品牌 型号 渠道 尺寸 网评分数(星级) 网评标题 网评内容
翻译 提炼优点(通过AI) 提炼缺点(通过AI) 赞同数 评论时间 评论链接
图片数量 图片地址 是否有视频 视频地址 商品ID 价格
```

价格表列 = `FIXED_HEAD` + `shops.columns` 展开 + `FIXED_TAIL`（见 §6.2）。

```jsonc
"export": {
  "brand_display": {"hisense": "海信", "tcl": "TCL"},   // 品牌 code → 表格展示名
  "size_from": "title"        // title | model | spec —— 尺寸从哪里推
}
```

---

## 8. `fetch` — 抓取策略

```jsonc
"fetch": {
  "protection": "L2_MEDIUM",     // L1_MILD | L2_MEDIUM | L3_STRONG
  "requires_browser": true,
  "interval_sec": 6.0,
  "retail_page_wait": "#productTitle",
  "reviews_page_wait": "[data-hook='review']"
}
```

对应 `SiteAdapter` 的 `protection` / `requires_browser` / `suggested_interval` /
`retail_page_wait`。`make_fetcher()` 按 `protection` 选 HttpFetcher 或 BrowserFetcher。

---

## 9. `expected` — 期望值（软校验）

```jsonc
"expected": {
  "match_rate_min": 0.90,        // 型号匹配成功率下限
  "price_fill_rate_min": 0.95,   // 有价格的商品占比
  "reviews_per_model_min": 0      // 评价数下限（0=不校验，很多型号确实没评价）
}
```

> 零售线**没有"找全率"**（型号清单是给定的），取而代之的红线是
> **型号匹配准确率**与**价格填充率**，见指标文档。

---

## 10. 完整示例（单店铺自营站）

```json
{
  "retail_spec_version": "0.1",
  "code": "liverpool_mx",
  "name": "Liverpool（墨西哥）",
  "country": "Mexico",
  "channel": "Liverpool",
  "region": "mx",
  "base_url": "https://www.liverpool.com.mx",
  "currency": "MXN",
  "capabilities": ["price", "reviews"],
  "status": "candidate",

  "anchor": "h1",
  "search": {
    "container": "a[href*='/producto/']",
    "sku": { "self_attr": "href" },
    "title": { "selectors": ["h3", "[class*='title']"], "transform": "clean_text" },
    "url": { "self_attr": "href" }
  },
  "product": {
    "title": { "selectors": ["h1"], "transform": "clean_text" },
    "brand": { "selectors": ["[class*='brand']"], "transform": "brand" },
    "size": { "selectors": ["h1"], "transform": "size_inch" }
  },
  "price": {
    "price": { "selectors": ["[class*='price'] span"], "transform": "money" },
    "currency": { "selectors": ["[class*='price']"], "transform": "currency_mx" },
    "in_stock": { "selectors": ["button[class*='cart']"], "transform": "in_stock" }
  },
  "summary": {
    "avg_rating": { "selectors": ["[class*='rating']"], "transform": "rating" },
    "total_count": { "selectors": ["[class*='reviewCount']"], "transform": "int" }
  },
  "reviews": {
    "container": "[class*='review-item']",
    "limit": 40,
    "fields": {
      "review_key": { "self_attr": "id" },
      "rating": { "selectors": ["[class*='stars']"], "attr": "aria-label", "transform": "rating" },
      "title": { "selectors": ["[class*='review-title']"], "transform": "clean_text" },
      "body": { "selectors": ["[class*='review-body']"], "transform": "clean_text" },
      "review_date": { "selectors": ["time"], "transform": "clean_text" }
    }
  },

  "match": {
    "strategy": "search_then_verify",
    "search_keyword_template": "{brand} {model}",
    "candidate_limit": 40,
    "verify": {
      "require_model_in": ["sku", "title"],
      "normalize": "upper_alnum",
      "allow_core_match": true,
      "core_strip_leading_size": true,
      "min_core_len": 4
    },
    "reject": {
      "title_contains_any": ["soporte", "montaje", "control remoto"],
      "url_contains_any": ["/accesorios/"]
    },
    "on_no_match": "skip"
  },
  "paginate": {
    "reviews": { "type": "query_param", "param": "page", "start": 1, "step": 1, "max_pages": 15 }
  },
  "incremental": { "key_field": "review_key", "stop_when": "page_all_known" },
  "shops": { "mode": "single_shop" },
  "export": { "size_from": "title" },
  "fetch": {
    "protection": "L2_MEDIUM", "requires_browser": true, "interval_sec": 6.0,
    "retail_page_wait": "h1"
  },
  "expected": { "match_rate_min": 0.90, "price_fill_rate_min": 0.95 }
}
```

---

## 11. 校验规则（`validate_retail_spec` 已实现）

> 2026-09-17：`overseas/retail_spec.py` 已实现 L0~L3 静态校验 + `load_retail_spec`。
> 三个负例（`on_no_match=first` / 站外 URL / 缺 `review_key`）全部被拒绝，见 `scripts/retail_spec_tests.py`。

| 层 | 检查 |
|---|---|
| L0 结构 | 必填字段齐（code/base_url/capabilities）；所有选择器可解析（lxml.cssselect）；正则可编译 |
| L1 安全 | 所有 URL 与 `base_url` 同源或在白名单（防 SSRF）；不含可执行代码 |
| L2 语义 | `capabilities` 含 `price` 时 `price` 段必填；含 `reviews` 时 `reviews.fields.review_key` 必填 |
| L3 红线 | `match.on_no_match` **必须**为 `skip`（`first` 直接拒绝）；`match.verify.require_model_in` 非空 |
| L4 实测 | 见技术方案的 Verify 阶段（真实页面命中 + 匹配准确率 + 价格填充率） |

---

## 12. 与引擎能力边界同步

和 SPEC 线一样，**schema 只能声明引擎已实现的能力**。新增枚举值必须同步：

| 常量（已建，`overseas/retail_spec.py`） | 当前取值 |
|---|---|
| `SUPPORTED_MATCH_STRATEGIES` | `search_then_verify` / `url_template` / `sku_map` |
| `SUPPORTED_REVIEW_PAGINATE` | `url_page` / `query_param` / `click_more` / `none` / `filter_sweep` / `bv_api` |
| `SUPPORTED_SHOP_MODES` | `multi_shop` / `single_shop` |
| `SUPPORTED_TRANSFORMS` | 见 `extract.py` 已实现的 transform 清单 |

---

## 13. 2026-09-20 扩展（Costco 接入时新增，均已实现）

接入 costco.ca / costco.com 时发现三类问题必须由 spec 表达，于是扩了下面这些字段。
**全部是可选字段，不写就是原行为，存量 spec 不受影响。**

### 13.1 `search.batch` — 品牌级搜索（防误匹配 + 防空转）★

```jsonc
"search": {
  "wait_ms": 8000,          // 搜索页渲染额外等待（毫秒）
  "scroll_passes": 8,       // 搜索页滚动次数（懒加载候选全靠它）
  "wait_selector": "a",     // 默认 a；★不要填候选容器（见下）
  "batch": {
    "keyword_template": "{brand} tv",
    "threshold": 40,        // 候选数 ≤ 此值走批量；force=true 时忽略
    "force": true,          // 无论型号多少都批量（慢站必开）
    "fallback_per_model": false,  // 批量候选里没有的型号直接 no_item，不再逐型号搜
    "max_candidates": 60,
    "min_candidates": 15,   // ★候选数下限：低于此值判"搜索失败"而非"型号不存在"
    "retries": 3,           // 搜索不达标时的重试次数
    "max_search_failures": 2 // 连续 N 个品牌搜索失败 → 停搜其余品牌
  }
}
```

语义分工（**红线**）：

| 情况 | 记什么状态 | 为什么 |
|---|---|---|
| 搜索页候选正常，但没有目标系列/尺寸 | `no_item` | 站上确实没上架 |
| 搜索页 0 候选 / 候选数低于下限 / 被拦 | `failed`，跳过该品牌 | 把抓取失败写成 `no_item` = **静默丢数据** |

`wait_selector` 必须是「页面一打开就存在」的元素（默认 `a`）。填候选容器会让
`wait_for_selector` 先白等 8 秒超时而候选是滚动后才出现的，拿不到更多候选。

### 13.2 `match.verify.core_size_tokens` — 尺寸与系列被拆开写时的尺寸守卫 ★

```jsonc
"verify": {
  "core_size_tokens": ["class", "inch", "in", "\"", "series"]
}
```

尺寸与系列之间夹了这些分隔词时（`Hisense 65" Class - U7SG Series`），
原来的紧贴式尺寸守卫 `(\d{2,3})<core>` 永远不命中 → 守卫静默失效 →
65 吋会被记成 75 吋的价。配上这组 token 后改用 `_candidate_sizes()` 抽全部尺寸再比对。
**不配则行为不变。**

配套：`explain_no_match()` 在未命中时给出可核对的原因
（该系列没有 / 同系列尺寸不符 / 候选全被 reject 排除 / 搜索页无候选）。

### 13.3 `reviews.source` + `reviews.bv` — 评价明细不在 DOM 里时走挂件接口

```jsonc
"reviews": {
  "source": "bazaarvoice_bfd",
  "bv": {
    "client": "Costco-EN_CA", "display_code": "20040_1_0",
    "site": "native_review_form", "locale": "en_CA",
    "origin": "https://www.costco.ca", "content_locale": "en_CA,en_US,fr_CA",
    "page_size": 100, "max_pages": 3, "max_reviews": 100
  },
  "fields": { "...": "DOM 路径的兜底声明，BV 路径不使用" }
}
```

对应 `paginate.reviews.type = "bv_api"`，由 `overseas/bv_reviews.py` 执行；
不写 `source` 时仍走原来的 DOM 评价 + 翻页逻辑。

### 13.4 `fetch.detail_http` — 搜索要浏览器、商品页走 HTTP

```jsonc
"fetch": { "requires_browser": true, "detail_http": true }
```

适用于 Costco 这类「搜索页客户端渲染、商品页服务端渲染」的混合站。
详情页单独用 `HttpFetcher`，单 PDP 从 10~20 秒降到 1~2 秒。

### 13.5 `Extractor` 新增 `self_text` 规则

```jsonc
"title": { "self_text": true }
```

搜索卡片本身就是 `<a>…标题…</a>`、卡片内没有稳定标题类名时，直接取卡片文本。
属 `extract.py` 的通用能力，非零售线专属。

---

> ⚠️ 本文档是 **v0.1 草案**。§3/§6 的字段名在实现 P0 任务时可能微调，
> 以代码为准并回来更新本文档。§13 的字段已随 Costco 接入落地并通过实测
> （见 `docs/Costco_CA_US_抓取可行性报告_2026-09-20.md`）。
