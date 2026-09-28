# 搜索直达详情页（landed_pdp）套路

**适用**：裸型号搜索会被站内 302 到商品详情页的自营站（liverpool 实测：`s=75U6SV` → `/tienda/pdp/...`）。
**识别信号**：探搜索页无候选卡片，URL/canonical 是 `/pdp/`；`open_dom` 可能报 `no_anchor_element`。

## 为什么发生

单店站搜索只返回「精确匹配唯一商品」时，常直接跳转该 PDP（亚马逊类也会）。页面没有
「搜索卡片列表」→ 搜索范型锚点选择器失效 → 被误判为反爬 blocked。

## 引擎已有兜底

`crawl_model_on_site` 的 `_search_landed_pdp(adapter, res, dom, html, model)`：

1. 从 `res.url` / canonical / og:url 提取规范 PDP URL（必须含 `/pdp/`）
2. 用 h1（或 URL slug 下划线化）构造单候选 `SearchHit`
3. 返回给 `match_product` 走三段匹配（型号必须出现在 sku/title 才命中）

## RetailSpec 配合

不建议生硬改 `search.container` 排除它——直达场景由兜底函数接管：

```jsonc
"search": {
  "container": "a[href*='/pdp/']:not([href*='category'])",
  ...
},
"match": {
  "search_keyword_template": "{brand} {model}"   // 带品牌降低直达概率；仍直达也能兜底
}
```

## 判定清单

- [ ] 该型号在站上有售（用 `probe.py --model` 确认）
- [ ] 搜索 URL 落 `/pdp/` 或 canonical 是 PDP
- [ ] `match_product` 对单候选能命中（`model in title`）

## 验收

- `retail-health` 用该站历史型号能命中（不误判 blocked）
- 全量型号里至少 1 个走直达路径且匹配正确