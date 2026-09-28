# 评价独立页 + 翻页（reviews_page）套路

**适用**：评价不在商品页首屏，而在独立评价页（如 Amazon 的 review 分页）。
**识别信号**：`detect_review_pagination` 判定为 `url_page` / `query_param` / `click_more`。

## 三种翻页形态

| type | 特征 | 例 |
|---|---|---|
| `url_page` | 页号嵌在 URL 路径里 | `.../review/<item>/Page=2/` |
| `query_param` | 页号在查询串 | `?pageNumber=2` / `?page=2` |
| `click_more` | 点「加载更多」按钮 | SPA 评价流 |

## RetailSpec 关键段

```jsonc
"paginate": {
  "reviews": {
    "type": "url_page",              // url_page | query_param | click_more | none
    "url_template": "https://review.kakaku.com/review/{item}/Page={page}/",  // url_page 用
    "param": "pageNumber",           // query_param 用
    "start": 1, "step": 1, "max_pages": 20
  }
},
"fetch": {
  "requires_browser": true,          // click_more 必然需要；url_page 可 HTTP
  "reviews_page_wait": "[data-hook='review']"
}
```

## review_key 必须用站内稳定 ID

- 用评价元素自身 `id` / `data-hook` / kakaku `ReviewCD` 等站内键。
- **禁止**「标题+作者」拼伪 key：排版变化会重复入库，增量去重失效。

## 增量停止

`incremental.stop_when`：
- `page_all_known`（默认）：本页评价全已知即停，最省请求
- `n_pages_no_new`：连续 N 页无新增才停（评价排序不稳的站用）

## 注意

- Amazon 类站评价懒加载，先滚动触发（`collect_detail` 默认 detail_scroll_passes=14）。
- HTTP 抓 lxml 拿不到 JS 渲染后的评价时，换 BrowserFetcher。
- 商品页即含评价（`reviews_url` 返回空串）的站不要配 `url_page`——`type: "none"` 即用商品页评价。