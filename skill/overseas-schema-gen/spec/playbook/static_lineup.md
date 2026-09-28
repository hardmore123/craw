# 套路：静态 lineup 一次列全

> 状态：✅ 引擎覆盖最好的结构，应优先保证。
> 已验证：秘鲁 Hisense（36 型号）、REGZA JP（39 型号）、Sony BRAVIA JP（8 型号）

---

## 识别信号

| 信号 | 期望值 |
|---|---|
| `load_more_hint` | `none` |
| `static_unique_links` | 与官网标称型号数同量级 |
| `link_growth.after_scroll` / `after_more` | 无明显增长（< 1.2 倍） |

⚠️ **陷阱**：`pager.has_next_button` 可能为 `true`，那是轮播/推荐区按钮。
只要没有 `has_numbered_pages`，就当静态站处理。

---

## schema 模板

```json
{
  "fetch": {
    "requires_browser": true,
    "interval_sec": 5.0,
    "entry_wait": "<产品链接选择器>",
    "entry_extra_wait_ms": 3000,
    "page_wait": "table, [class*='spec']",
    "scroll_until_stable": true,
    "scroll_growth_selector": "<产品链接选择器>"
  },
  "discover": {
    "mode": "dom_anchor",
    "link_selector": "<产品链接选择器>",
    "model_url_regex": "<从URL提型号，含1个捕获组>",
    "series_rule": "strip_leading_size",
    "non_tv_filter": true
  },
  "spec": { "extract_order": ["generic_spec_js"] },
  "identity": { "series_key": "series_name", "model_normalize": "upper_alnum" },
  "expected": { "series_count": null, "model_count": <去官网数一遍> }
}
```

**关键点**：
- 就算是静态站，也建议留 `scroll_until_stable: true` + `scroll_growth_selector`——
  懒加载注入的站（REGZA）靠它保证元素都渲染出来，无副作用。
- **绝对不要**写 `discover.paginate`。Sony 就是因为误加分页，实抓从 8 掉到 0~1。
- `entry_extra_wait_ms` 对 JS 延迟注入站很关键（Sony 类），3000~6000 之间。

---

## 三个实战参数对照

| 站点 | `link_selector` | `model_url_regex` |
|---|---|---|
| 秘鲁 Hisense | `a[href*='/tv/']` | `/tv/[^/]*-([0-9]{2}[a-z0-9]+)$` |
| REGZA JP | `a[href*='/tv/lineup/']` | `/tv/lineup/([^/?#]+)` |
| Sony BRAVIA JP | `a[href*='/bravia/products/']` | `/bravia/products/([A-Za-z0-9-]+)/` |

注意后两个都做了**路径收窄**（不用裸 `/lineup/` 或 `/products/`），原因见
`reference.md` §1.2。

---

## 常见失败与修法

| 症状 | 修法 |
|---|---|
| 发现数远大于期望 | 选择器太宽泛，收窄到电视专属路径；开 `non_tv_filter` |
| 发现数为 0 或极少 | `entry_extra_wait_ms` 加到 6000；确认 `entry_wait` 用的是产品链接选择器而非 `body` |
| 发现数只有期望的一半左右 | 可能其实不是静态站，重跑 `--entry` 看 `link_growth`；或站点有隐藏分页 |
| 混入 soundbar / 投影 / 其它品类 | 开 `non_tv_filter: true`；仍不干净则收窄选择器 |
