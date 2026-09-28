# 套路：编号页码分页（含替换式渲染）

> 状态：✅ 引擎支持，通用片段已鲁棒化
> 已验证：Philips CA 25/25 = 100%

---

## 识别信号

| 信号 | 说明 |
|---|---|
| `load_more_hint` = `paginate_replace` | 替换式分页：点击换掉当前页，链接数**不增长** |
| `load_more_hint` = `click_more_or_paginate` | 追加式：点击后链接数增长 |
| `pager.has_numbered_pages` = `true` | ★**唯一可靠的分页信号** |

两种都用同一套 schema 配置，区别由片段内部处理。

⚠️ **只有编号页码才算分页**。孤立的 next 按钮不算（见 `reference.md` §2.1）。

---

## schema 模板

```json
{
  "fetch": {
    "requires_browser": true,
    "interval_sec": 5.0,
    "entry_wait": "<产品链接选择器>",
    "entry_extra_wait_ms": 5000,
    "page_wait": "h1, h2",
    "scroll_until_stable": true,
    "scroll_growth_selector": "<产品链接选择器>",
    "scroll_max_passes": 80
  },
  "discover": {
    "mode": "dom_anchor",
    "link_selector": "<产品链接选择器>",
    "model_url_regex": "<含1个捕获组>",
    "series_rule": "strip_leading_size",
    "non_tv_filter": true,
    "paginate": {
      "type": "js_snippet",
      "snippet_ref": "numbered_pages_generic"
    }
  },
  "spec": { "extract_order": ["embedded_json", "generic_spec_js"] },
  "identity": { "series_key": "series_name", "model_normalize": "upper_alnum" },
  "expected": { "series_count": null, "model_count": <官网标称数> }
}
```

---

## 分页方式二选一

引擎支持两种 `paginate.type`：

| type | 何时用 |
|---|---|
| `js_snippet` | 前端渲染分页（点页码/下一页按钮切换，URL 不变或用 hash）。见下方片段选型 |
| `query_param` | **服务端分页**：翻页体现在 URL 查询参数上（LG `?firstResult=30`）。见本文件末尾 |

判断方法：翻页时看 URL 变不变。变了且带页码/偏移参数 → `query_param`（更稳，首选）；
URL 不变、纯前端 DOM 切换 → `js_snippet`。

### js_snippet 片段选型

| 片段 | 何时用 |
|---|---|
| `numbered_pages_generic` | **默认首选**。已鲁棒化：每翻页重扫页码 + `visited` 队列翻到尽头 + 无编号时回退点下一页 |
| `philips_paginate` | 仅当通用片段在 Philips 站失效时。站点专用，`aria-label='Show page N of results'` 写死 |

> 历史注意：一次 onboard 中 LLM 选了通用片段只发现 12/25，当时以为是通用片段太保守，
> 实际根因是片段内 `pageBtn` 正则的三层转义 bug（`'\\\\b'` 应为 `'\\b'`）。
> 修复后通用片段就能 25/25。**所以不要因为"通用片段抓不全"就急着换专用片段，先确认不是 bug。**

---

## 找全率的正确口径 ★关键

分页站的找全率**不能用首屏 HTML 算**：

| 算法 | Philips CA 结果 |
|---|---|
| 用首屏 HTML（只有第 1 页 12 个） | 48% ❌ 误判失败 |
| 用引擎执行分页片段后的实际发现数 | **100%** ✅ 正确 |

`probe.py --verify` 用的是缓存的入口 HTML，对分页站可能偏低。
**分页站必须再跑一次真实抓取确认**：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/<code>.spec.json
```

看输出的 `series=N models=M` 才是真实找全数。

---

## 常见失败与修法

| 症状 | 修法 |
|---|---|
| 只发现第 1 页数量 | 片段没生效：确认 `paginate.type` 是 `js_snippet` 且 `snippet_ref` 拼写正确 |
| 发现数在 1~2 页之间 | 片段翻页中断：站点页码控件结构特殊，考虑 `philips_paginate` 或记为缺口 |
| 抓到 0 个 | 点错按钮把页面导航走了 —— 确认真有编号页码，否则删掉 paginate 段 |
| 有重复型号 | 正常，引擎按型号 `setdefault` 去重；若系列数异常再查 `series_rule` |

---

## query_param 分页（LG ?firstResult=N 型）

服务端分页：翻页参数直接在 URL 上，引擎逐页拼 URL 请求、累积去重型号。
**比 js_snippet 稳**——不依赖前端渲染，纯 HTTP 也能翻。

### 配置

```json
"discover": {
  "mode": "dom_anchor",
  "link_selector": "a[href*='/tv-soundbars/']",
  "model_url_regex": "/tv-soundbars/([a-z0-9]+)",
  "series_rule": "strip_leading_size",
  "paginate": {
    "type": "query_param",
    "param": "firstResult",
    "step": 30,
    "max_pages": 50,
    "stop_rounds": 2
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `param` | 是 | 分页参数名。看 URL：`?firstResult=30` → `firstResult` |
| `step` | 否 | 每页步长，默认 30。看第 2 页的参数值推断（第 2 页 =30 则 step=30） |
| `max_pages` | 否 | 安全阀，默认 50，防死循环 |
| `stop_rounds` | 否 | 连续 N 页无新增即停，默认 2 |

引擎行为：offset 从 0 按 step 递增，逐页 `?param=offset` 请求，
每页型号累积去重，连续 `stop_rounds` 页无新增或到 `max_pages` 停。

### 常见失败

| 症状 | 修法 |
|---|---|
| 只拿到第 1 页 | `param` 名写错；或站点分页参数不是这个（看 URL 确认） |
| step 不对导致漏页/重复页 | 对照真实第 2 页 URL 的参数值设 step |
| 到不了尽头就停 | `stop_rounds` 太小（某些站中间有空洞页），调大到 3 |
| 跑很久不停 | 站点对越界 offset 仍返回首页（无限重复）→ 靠 max_pages 兜底，或改小 |

### 找全率口径

同分页站：`probe.py --verify` 用首屏 HTML 会低估，**必须 spec-crawl 后看审计**
的 `discovered_model_count` 对比官网标称。
