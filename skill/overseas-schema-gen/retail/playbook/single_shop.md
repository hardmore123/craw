# 自营站（single_shop）套路

**适用**：Amazon / Liverpool / Coppel / Palacio 等自营站——一个型号一个价，评价在同一商品页。
**识别信号**：`detect_multi_shop` 返回 `{mode: single_shop}`；页面价格区只有单一价格节点。

## RetailSpec 关键段

```jsonc
{
  "capabilities": ["price", "reviews"],
  "search": {
    "container": "a[href*='/pdp/']",        // 候选链接（相对 href 会转绝对）
    "sku": { "self_attr": "href" },
    "title": { "selectors": ["h3"], "transform": "clean_text" },
    "url": { "self_attr": "href" }
  },
  "product": {
    "title": { "selectors": ["h1"], "transform": "clean_text" },
    "brand": { "selectors": ["[class*='brand']"], "transform": "brand" },
    "size": {                                  // ★ h1 标题兜底必加
      "selectors": ["h1"],
      "kv": { "container": "table tr", "key_contains": "tamaño|pulgadas|size",
              "value": "td" },
      "transform": "size_inch"
    }
  },
  "price": {
    "price": { "selectors": ["[class*='price']", "[data-testid*='price']", "[class*='precio']"],
               "transform": "money",
               "jsonld": "offers.price" },           // 兜底
    "currency": { "selectors": ["h1"], "transform": "currency_mx" },
    "list_price": { "selectors": ["s", "del", "[class*='old']"], "transform": "money" },
    "in_stock": { "selectors": ["button:not([disabled])"], "transform": "bool_present" }
  },
  "summary": { "avg_rating": { "selectors": ["[class*='rating']"], "transform": "rating" } },
  "reviews": {
    "container": "[class*='review'], [data-testid*='review']",
    "limit": 40,
    "fields": {
      "review_key": { "self_attr": "id" },
      "rating": { "selectors": ["[class*='star']"], "transform": "rating" },
      "title": { "selectors": ["h3"], "transform": "clean_text" },
      "body": { "selectors": ["p"], "transform": "clean_text" }
    }
  },
  "match": {
    "strategy": "search_then_verify",
    "search_keyword_template": "{brand} {model}",   // ★1-ring 必须带品牌，否则裸型号常 302 直达 PDP
    "verify": { "require_model_in": ["sku", "title"], "allow_core_match": true },
    "reject": { "title_contains_any": ["soporte", "control", "funda", "cable"] },
    "on_no_match": "skip"
  },
  "shops": { "mode": "single_shop" },
  "fetch": { "requires_browser": true, "protection": "L2_MEDIUM", "interval_sec": 5.0 }
}
```

## 注意事项

- **搜索关键词带品牌**：`search_keyword_template: "{brand} {model}"`。裸型号薄命中常
  302 直达 PDP（页面无卡片）→ 需要 `_search_landed_pdp` 兜底。
- **尺寸 h1 兜底**：规格表 kv 不总是存在；不给 h1 兜底会尺寸列空。
- **评价懒加载**：评价区常需浏览器滚动；`--no-reviews` 仅调试，正式要 `requires_browser`。
- **短型号词边界**：`A4K` 不要撞商品描述里 "a 4K"；精确匹配保留词边界（引擎已处理）。

## 验收

- `retail-health --spec x --models-file 真实型号清单 --max-models 3` → 选择器命中 + 填充率 ≥95%
- 全量跑：抽检 20 个零误匹配
- 导出价格表对齐日本线模板（13 列 + 品牌分组行）