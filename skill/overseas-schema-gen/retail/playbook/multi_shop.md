# 聚合站（multi_shop）套路

**适用**：kakaku.com 这类比价/聚合站——一个商品页列多家店铺报价。
**识别信号**：`detect_multi_shop` 命中 `p-price(list|table|_row)`/`店名`→ multi_shop。

## RetailSpec 关键段

```jsonc
{
  "shops": {
    "mode": "multi_shop",
    "list": {
      "container": "li.p-priceList_item",          // 价格表每行（店铺+价）
      "shop_name": ".p-priceList_shopName",        // 店铺名选择器
      "price": ".p-priceList_price",               // 店铺价格选择器
      "limit": 30
    },
    "columns": [                                   // ★导出列声明（该国店铺列）
      {"key": "ヨドバシ.com", "display": "友都八喜(Yodobashi.com)", "match": ["ヨドバシ"]},
      ...
    ],
    "lowest": {"price": ".p-lowest_price", "shop": ".p-lowest_shop"}
  },
  "match": { "strategy": "url_template", "on_no_match": "skip", "verify": { "require_model_in": ["sku", "title"] } }
}
```

## 注意事项

- **`shops.columns` = 导出核心**：`price_export.build_columns(spec)` 用它做店铺段列。
  新增国家先写 columns（display 中文表头），否则导出一列空。
- **`map_shop_columns` 把原始店铺名映射到列**：按 `columns[].match` 关键词子串匹配，
  同一列多店取最低价；未命中列导出为空（不是错误）。
- **`extract_shop_prices` 抽 {原始店名: 价}**，再用 `map_shop_columns` 映射。
- 参考 `docs/retail_specs/kakaku_jp.retail.json`（已与日本线 8 列逐字一致验证）。
- `price_export` 不传 spec 默认日本 8 列（向后兼容），聚合站在 spec 里声明 columns 即可。

## 验收

- 导出价格表与当前 kakaku 8 店铺列逐字一致（回归）
- 店铺列从 spec 读，不依赖代码常量