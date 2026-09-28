# 套路：滚动懒加载追加

> 状态：✅ 引擎支持（`scroll_until_stable` 走 `open_dom` 的滚动到稳定能力）
> 已验证：暂无纯滚动站完整实测（REGZA 是懒加载注入但首屏已列全，归 static_lineup）

---

## 识别信号

| 信号 | 期望值 |
|---|---|
| `load_more_hint` | `scroll` |
| `link_growth.after_scroll` | 明显大于 `static_unique_links`（> 1.2 倍） |
| `pager.has_numbered_pages` | `false`（有编号页码就该走 numbered_paginate） |

---

## schema 模板

```json
{
  "fetch": {
    "requires_browser": true,
    "interval_sec": 5.0,
    "entry_wait": "<产品链接选择器>",
    "entry_extra_wait_ms": 4000,
    "page_wait": "table, [class*='spec']",
    "scroll_until_stable": true,
    "scroll_growth_selector": "<产品链接选择器>",
    "scroll_max_passes": 80
  },
  "discover": {
    "mode": "dom_anchor",
    "link_selector": "<产品链接选择器>",
    "model_url_regex": "<含1个捕获组>",
    "series_rule": "strip_leading_size",
    "non_tv_filter": true
  },
  "spec": { "extract_order": ["generic_spec_js"] },
  "identity": { "series_key": "series_name", "model_normalize": "upper_alnum" },
  "expected": { "series_count": null, "model_count": <官网标称数> }
}
```

**关键点**：
- `scroll_growth_selector` **必须**填产品链接选择器（不是 `a[href]`）——
  用它判断"还在增长"，填太宽泛会因导航链接不变而提前判稳定。
- `scroll_max_passes` 是安全阀不是目标值，80 足够；滚动到连续 2 轮不增长自动停。
- 依然**不写** `discover.paginate`——滚动加载不是分页。

---

## 常见失败与修法

| 症状 | 修法 |
|---|---|
| 只拿到首屏数量 | `scroll_until_stable` 漏了，或 `scroll_growth_selector` 填错/为空 |
| 滚到一半就停 | 增大 `scroll_max_passes`；站点可能滚动到某点需点击"加载更多" → 转 numbered_paginate |
| 每次跑结果数量不稳定 | 加大 `entry_extra_wait_ms`；网络抖动导致部分批次没加载完 |

---

## 与 Load More 的区分

滚动加载和"点击 Load More 加载"探测信号不同：

| | `load_more_hint` |
|---|---|
| 纯滚动追加 | `scroll` |
| 需点击按钮追加 | `click_more_or_paginate` |

若 `probe.py` 报 `none` 但首屏数量远小于期望，很可能是 **Load More 按钮没被通用候选点开**
（Hisense US 就是这样）→ 走 `needs_handwritten.md`。
