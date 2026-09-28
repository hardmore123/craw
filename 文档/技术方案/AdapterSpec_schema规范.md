# AdapterSpec schema 规范

> 版本：**v1.1**（2026-09-16）
> 状态：**基线冻结**。基于 `docs/specs/philips_ca.spec.json` 实战样本 +
> `generic_spec.py` / `spec_generator.py` 代码逐字段核对而成。
> 后续遇到覆盖不全的站点再增补字段，**增补必须同步改引擎白名单常量**（见 §6）。

---

## 0. 这份规范的定位

AdapterSpec 是 **skill / LLM 与执行引擎之间的唯一契约**。

```
探测 → 判断 → 产出 AdapterSpec(JSON) → generic_spec 引擎执行 → 抓全站 SPEC
                    ↑ 本规范约束这一段
```

三条铁律：

1. **只产配置，不产代码。** 需要执行 JS 时只能 `snippet_ref` 引用已登记片段库，不能内联 JS 源码。
2. **字段必须在引擎已实现范围内。** 写了引擎不认的字段 = 静默失效；写了引擎白名单外的枚举值 =
   `NotImplementedError` 或校验拒绝。
3. **本规范与代码不一致时，以代码为准**，并回来修本文档。已核对的代码位置标注在各节。

---

## 1. 完整字段表

### 1.1 顶层标识段

| 字段 | 类型 | 必填 | 引擎读取位置 | 说明 |
|---|---|---|---|---|
| `spec_version` | string | 是 | — | 固定 `"1.0"`（schema 自身版本，暂不随文档版本变） |
| `code` | string | 是 | `build_generic_adapter` | 站点代号，如 `philips_ca`、`regza_jp`。小写 + 下划线，`<品牌>_<地区>` |
| `brand_name` | string | 是 | → `adapter.name` | 展示名，如 `Philips（加拿大）` |
| `region` | string | 是 | — | 地区码 `ca` / `jp` / `mx` / `us` / `pe`，参与 region 边界校验 |
| `country` | string | 建议 | → `adapter.country` | 导出表「所属国家」列，如 `Canada` |
| `channel` | string | 建议 | → `adapter.channel` | 导出表「渠道」列，如 `Philips 官网` |
| `base_url` | string | 是 | 发现时拼相对链接 | 形如 `https://www.philips.ca`，**不带尾斜杠** |
| `entry_url` | string | 是 | `spec_entry_url()` | 电视总览/lineup 入口页 |
| `fallback_entry_urls` | string[] | 否 | 发现时依次重试 | 主入口抓不到时按序尝试 |
| `generated_by` | string | 建议 | 写入审计 `details` | 来源留痕，如 `skill:spec-schema-gen/2026-09-16` |
| `status` | string | 建议 | — | `candidate` / `approved` / `deprecated` / `needs_human` |

### 1.2 `fetch` — 抓取与等待策略

全部由 `build_generic_adapter` 映射成适配器类属性。

| 字段 | 类型 | 默认 | 映射到 | 说明 |
|---|---|---|---|---|
| `protection` | string | `L2_MEDIUM` | `adapter.protection` | ⚠️ `PROTECTION_MAP` **只登记了 `L2_MEDIUM`**，其它值一律回落到 `L2_MEDIUM` |
| `requires_browser` | bool | `true` | `adapter.requires_browser` | 电视站基本都要 `true`（SPA 渲染） |
| `interval_sec` | number | `6.0` | `adapter.suggested_interval` | 抓取间隔，实际取 `max(全局配置, 本值)` |
| `entry_wait` | string | `body` | `spec_entry_wait` | 入口页等待锚点。**建议直接写产品链接选择器**，比 `body` 可靠得多 |
| `entry_extra_wait_ms` | int | `0` | `spec_entry_extra_wait_ms` | JS 延迟注入站需要（Sony BRAVIA 类），建议 3000~6000 |
| `page_wait` | string | `table` | `spec_page_wait` | 型号规格页等待锚点 |
| `scroll_until_stable` | bool | `false` | `spec_entry_scroll_until_stable` | 懒加载站置 `true` |
| `scroll_growth_selector` | string | `""` | `spec_entry_scroll_growth_selector` | 判断"还在增长"的选择器，通常同 `link_selector` |
| `scroll_max_passes` | int | `80` | `spec_entry_scroll_max_passes` | 滚动次数安全阀 |

### 1.3 `discover` — 发现型号（核心段）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | ⚠️ **仅 `"dom_anchor"`（单层）或 `"dom_anchor_two_level"`（两级）可用**。其它值引擎直接 `raise NotImplementedError` |
| `link_selector` | string | 单层必填 | 产品链接 CSS 选择器。缺省时回落用 `fetch.entry_wait` |
| `model_url_regex` | string | 是 | 从链接 URL 提型号，**必须含至少 1 个捕获组**，组 1 即型号。引擎用 `re.I` 编译 |
| `series_rule` | string | 否 | ⚠️ **当前仅 `"strip_leading_size"`**（去型号前导 2~3 位尺寸数字）。未识别的值静默回落到该规则 |
| `non_tv_filter` | bool | 否 | `true` 时启用 `clearly_non_tv()` 过滤 soundbar / 投影 / 支架等 |
| `paginate` | object | 否 | 见下表。**静态站不要写这一段** |

**两级发现专用字段**（仅 `mode="dom_anchor_two_level"` 时用）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `series_link_selector` | string | 是 | 入口页 → 系列页的链接选择器 |
| `series_url_regex` | string | 是 | 从系列页 URL 提系列名，**必须含至少 1 个捕获组**，组 1 即系列名 |
| `model_link_selector` | string | 是 | 系列页 → 型号页的链接选择器 |
| `series_page_wait` | string | 否 | 打开系列页时的等待锚点，缺省用 `model_link_selector` |
| `series_from` | string | 否 | 填 `"model"` 时按型号号用 `series_rule` 归并系列；缺省用 `series_url_regex` 捕获的系列名 |

> 两级模式下 `link_selector` 不用填（型号不在入口页）。`model_url_regex` 在此处
> 表示"从**型号页** URL 提型号"。发现流程：入口页 →（series_link_selector +
> series_url_regex）系列页 →（model_link_selector + model_url_regex）型号页。

**接口发现专用字段**（仅 `mode="xhr_api"` 时用，写在 `discover.api` 对象里）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url_template` | string | 是 | 接口 URL，**必须与 base_url 同源**（SSRF 校验拦站外域名） |
| `list_path` | string | 是 | 产品列表在 JSON 中的点分路径，如 `response.resultData.productList`；数组下标用整数段 |
| `model_field` | string | 是 | 列表项里的型号字段名 |
| `url_field` | string | 否 | 详情页 URL 字段名 |
| `model_url_template` | string | 否 | 无 `url_field` 时按型号拼 URL，`{model}` 占位 |
| `series_field` | string | 否 | 系列名字段；缺省按 `series_rule` 从型号推 |
| `family_id_field` | string | 否 | 系列身份键，**优先作归并键防同名系列覆盖**，配 `identity.series_key=family_id` |

`discover` 下另有 `fallback_dom`（bool）：置 `true` 且给了 `link_selector` /
`model_url_regex` 时，接口不可达/非 JSON/list_path 落空则自动回退 `dom_anchor` 单层。

> xhr_api 模式 `model_url_regex` 不必填（型号来自接口字段）。找全率无法从入口页
> HTML 判定，`probe.py --verify` 只校验配置完整性，需 `spec-crawl` 后看审计
> `item_count` / `discovered_model_count`。

`discover.paginate`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | `"js_snippet"`（JS 片段翻页）或 `"query_param"`（URL 查询参数翻页） |
| `snippet_ref` | string | `type=js_snippet` 时用。只能取 `"numbered_pages_generic"` 或 `"philips_paginate"`，其它值 `validate_spec` 拒绝 |
| `param` | string | `type=query_param` 时**必填**，分页参数名（如 `firstResult`） |
| `step` | int | `type=query_param` 步长，默认 30 |
| `max_pages` | int | `type=query_param` 安全阀，默认 50，防死循环 |
| `stop_rounds` | int | `type=query_param` 连续 N 页无新增即停，默认 2 |

> `query_param` 用法：引擎对入口 URL 逐页追加 `?<param>=<offset>`（offset 按 step
> 从 0 递增），累积去重型号，连续 `stop_rounds` 页无新增或到 `max_pages` 停。
> 适用 LG `?firstResult=N` 这类服务端分页站。

> **选型建议**：编号页码型分页优先 `numbered_pages_generic`（已鲁棒化，Philips CA 实测 25/25）。
> `philips_paginate` 是站点专用，仅在通用片段失效时用。

### 1.4 `spec` — 规格页解析

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `extract_order` | string[] | 是 | 按序尝试，第一个出结果即停。⚠️ 只能用 `embedded_json` / `embedded_json_philips` / `generic_spec_js` |
| `embedded_json` | object | 用 `embedded_json` 时必填 | 内嵌 JSON 的字段名映射，见下 |

`spec.embedded_json`（字段名全部是"页面内嵌 JSON 里的键名"）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `root_marker` | `specification` | 承载规格的根键名 |
| `chapters_field` | `csChapter` | 分组数组字段。**同时用作 `require_field`**——只接受含此字段的根对象，避免命中同名但无规格的对象 |
| `chapter_name_field` | — | 分组显示名 |
| `chapter_code_field` | — | 分组代码 |
| `items_field` | `csItem` | 条目数组 |
| `item_name_field` | — | 条目名（规格项，如「屏幕尺寸」） |
| `values_field` | `csValue` | 值数组 |
| `value_name_field` | — | 值显示名 |
| `value_join` | ` / ` | 多值拼接分隔符 |

> `generic_spec_js` 无需配置——它是通用多策略 DOM 抽取（table / dl / 键值对），
> 作为 `extract_order` 的**兜底档**，建议始终放在最后一位。

### 1.5 `identity` — 身份与去重

| 字段 | 类型 | 说明 |
|---|---|---|
| `series_key` | string | `series_name` / `family_id` / `url_slug`。防 Samsung 那类同名系列覆盖 |
| `model_normalize` | string | `upper_alnum`（引擎内部统一 `.upper()`） |

### 1.6 `expected` — 期望值（软校验）

| 字段 | 类型 | 说明 |
|---|---|---|
| `series_count` | int \| null | 官网标称系列数 |
| `model_count` | int \| null | 官网标称型号数。**`score_spec_on_html` 用它算找全率，<80% 直接否决** |

> `expected` 填不填决定了"找全率"这道闸门有没有效。**强烈建议人工数一遍官网数量填进来**——
> 这是防"抓了一半当成功"的最后一道防线。

---

## 2. 最小可用示例（静态列全站）

适用：秘鲁 Hisense / REGZA 这类首屏一次列全的站。

```json
{
  "spec_version": "1.0",
  "code": "hisense_pe",
  "brand_name": "Hisense（秘鲁）",
  "region": "pe",
  "country": "Peru",
  "channel": "Hisense 官网",
  "base_url": "https://www.hisense.com.pe",
  "entry_url": "https://www.hisense.com.pe/tv",
  "generated_by": "skill:spec-schema-gen/2026-09-16",
  "status": "candidate",

  "fetch": {
    "requires_browser": true,
    "interval_sec": 5.0,
    "entry_wait": "a[href*='/tv/']",
    "entry_extra_wait_ms": 3000,
    "page_wait": "table, [class*='spec']",
    "scroll_until_stable": true,
    "scroll_growth_selector": "a[href*='/tv/']"
  },

  "discover": {
    "mode": "dom_anchor",
    "link_selector": "a[href*='/tv/']",
    "model_url_regex": "/tv/[^/]*-([0-9]{2}[a-z0-9]+)$",
    "series_rule": "strip_leading_size",
    "non_tv_filter": true
  },

  "spec": {
    "extract_order": ["generic_spec_js"]
  },

  "identity": { "series_key": "series_name", "model_normalize": "upper_alnum" },
  "expected": { "series_count": 13, "model_count": 36 }
}
```

完整示例（分页站 + 内嵌 JSON）见 `docs/specs/philips_ca.spec.json`。

---

## 3. 产出位置约定

| 用途 | 路径 | 是否入库 |
|---|---|---|
| 已验收、要长期复用 | `docs/specs/<code>.spec.json` | 入库 |
| skill 探索中的草稿 | `data/schema_drafts/<code>.spec.json` | 不入库（`data/` 已被忽略） |

⚠️ **不要**放到 `overseas/sites/<code>/schema.json`。那个路径是零售线 `Extractor` 的
schema 格式（`selectors` / `transform` / `search` 那一套），与 AdapterSpec 是**两种不同的格式**，
放错位置会被 `SiteAdapter.__init__` 当成抽取 schema 加载。

用法：

```powershell
# 用草稿 spec 直接跑全链路抓取（走 generic_spec 引擎）
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/hisense_pe.spec.json
```

---

## 4. 校验流程（产出后必跑，不可跳过）

两层，缺一不可：

```python
from overseas.spec_generator import validate_spec, score_spec_on_html

# 第一层：静态合法性（字段齐、正则可编译、枚举在白名单、域名同源防 SSRF）
problems = validate_spec(spec, probe_report)      # 空列表 = 通过

# 第二层：真实页面实测打分（静态校验查不出"选择器语法对但页面没这元素"）
report = score_spec_on_html(spec, entry_html, threshold=70.0)
# report: {score, passed, checks, failures, discovered_models, coverage}
```

实测打分四维（权重）与**硬否决条件**：

| 维度 | 权重 | 硬否决 |
|---|---|---|
| `link_selector` 命中产品链接 | 3 | 未命中 → 直接否决 |
| 型号可提取且格式合理（字母+数字混合） | 3 | 提不到 → 直接否决 |
| 找全率 vs `expected.model_count` | 2 | **< 80% → 直接否决** |
| 型号页规格可解析（≥3 行） | 2 | 软指标 |

> 分页站要注意口径：找全率**不能用首屏 HTML 卡分**，要用引擎执行分页片段后的实际发现数
> （这是 §9.14 踩过的坑）。

---

## 5. 常见错误对照

| 症状 | 原因 | 修法 |
|---|---|---|
| `NotImplementedError: discover.mode=xhr_api` | 引擎未实现该模式 | 只能用 `dom_anchor`；确实需要接口发现 → 记为引擎缺口 |
| `paginate.snippet_ref 未登记` | 片段名不在 `KNOWN_SNIPPETS` | 改用 `numbered_pages_generic`，或先给片段库加片段 |
| 发现数远小于官网数 | 分页/懒加载没配 | 静态站补 `scroll_until_stable`；分页站补 `paginate.snippet_ref` |
| 发现数远大于官网数 | 选择器太宽泛，混入导航/其它品类 | 收窄 `link_selector` 到产品专属路径；开 `non_tv_filter` |
| 规格行数为 0 | `extract_order` 选错档或 `embedded_json` 字段名不对 | 兜底加 `generic_spec_js`；核对内嵌 JSON 真实键名 |
| 静态站被加了分页，反而抓到 0 | 误把轮播 next 按钮当分页 | 删掉 `discover.paginate` |

---

## 6. 扩展流程（新增字段/枚举值时必须同步）

发现现有 schema 覆盖不了新站时，**不要在 schema 里私自造字段**——引擎不认就是静默失效。
正确流程：

1. 在引擎实现能力（`generic_spec.py` 加 mode 分支 / 片段 / 解析方法）；
2. 同步更新三个白名单常量（`spec_generator.py`）：
   - `SUPPORTED_DISCOVER_MODES`
   - `KNOWN_SNIPPETS`
   - `SUPPORTED_EXTRACT_METHODS`
3. 更新本文档字段表 + `_SYSTEM_PROMPT` 里的约束说明；
4. 在 `.kiro/skills/spec-schema-gen/playbook/` 补一份该结构型套路；
5. 跑 Philips CA 回归（`spec-crawl --site philips_ca` 应仍是 11 系列 25 型号）。

### 当前引擎能力边界一览（截至 2026-09-16）

```
discover.mode        : dom_anchor, dom_anchor_two_level, xhr_api      （3 种）
paginate.type        : js_snippet, query_param                        （2 种）
paginate.snippet_ref : numbered_pages_generic, philips_paginate       （2 个）
extract_order        : embedded_json, embedded_json_philips,
                       generic_spec_js                               （3 种）
series_rule          : strip_leading_size                             （1 种）
protection           : L2_MEDIUM                                      （1 种）
```

**P0 覆盖面缺口已全部落地**：多级发现（dom_anchor_two_level）、接口发现（xhr_api）、
query_param 分页均已实现。后续扩展见任务表 P1/P2（生产化与合规）。
