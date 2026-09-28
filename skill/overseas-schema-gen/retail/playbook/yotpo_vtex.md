# 套路：Yotpo 挂件评价直采（VTEX 站常见）

> 适用站点：VTEX 平台零售站但评价挂件走 Yotpo（非 VTEX 原生 reviewsByProductId、非 BazaarVoice）。
> 已验证：2026-09-24，elektra_gt（Elektra Guatemala，GTQ）。

## 什么时候用

VTEX 站点的 PDP 评价区如果由 Yotpo 渲染，**不要试 VTEX GraphQL reviewsByProductId**（返 400
GraphQL validation failed），也不要试 BV —— Yotpo 暴露了公开 Storefront API，可纯 HTTP 直连取评价明细。

识别 Yotpo 站的特征（PDP 网络拦截）：
- 加载 `https://staticw2.yotpo.com/{app_key}/widget.js`
- 加载 `https://api-cdn.yotpo.com/v3/storefront/store/{app_key}/product/{pid}/reviews`
- VTEX bundle 含 `react~vtex.yotpo@x.x.x`
- DOM 有 `.yotpo-main-widget` / `[data-product-id]` 容器

## 关键参数

- **app_key**（store_id）：从 `staticw2.yotpo.com/{app_key}/widget.js` 路径段取，静态固定。
  elektra_gt = `Yx5PqPQGSeQzSjdlNcyjpBRu1TmbaWWvbGqKmpbp`
- **product_id**：Yotpo 的 product_id = 站点商品号。VTEX 站通常 = DB `product.sku`
  （DOM `[data-product-id]` 与 DB sku 一致）。用 DB sku 直接查即可。

## API 端点（v1 Storefront，公开无认证）

```
GET https://api-cdn.yotpo.com/v1/widget/{app_key}/products/{product_id}/reviews.json?per_page=150&page={n}
```

- **无需 token / cookie / Origin / Referer**，代理可访问，纯 HTTP 即可
- `per_page` 最大 150；`page` 从 1 起
- 返回 JSON：
  ```json
  {"response": {
    "pagination": {"page":1, "per_page":150, "total": <总数>},
    "bottomline": {"total_review":N, "average_score":4.5,
                   "star_distribution": {"1":x,"2":x,"3":x,"4":x,"5":x}},
    "reviews": [ {id, score, content, title, verified_buyer, created_at,
                  user:{display_name}, votes_up, images_data} ]
  }}
  ```

## 字段映射 → review / review_summary

```python
review_key = f"yotpo:{r['id']}"        # 站内唯一，增量去重
rating     = r["score"]                 # 1-5
body       = r["content"]
title      = r["title"]
author     = r["user"]["display_name"]
review_date= r["created_at"]           # ISO 2022-07-18T12:59:05.000Z
verified   = 1 if r["verified_buyer"] else 0
helpful_count = r.get("votes_up")
image_urls = " | ".join(im["original_url"] for im in (r.get("images_data") or []))

# summary
avg_rating  = bottomline["average_score"]
total_count = bottomline["total_review"]
star1..5    = bottomline["star_distribution"]["1".."5"]
```

## 红线（宁留空不造假）

- 0 评价产品（`bottomline.total_review == 0` 且 `reviews` 为空）**不写 review 不写 summary**
- 只有 `total_review > 0` 才写 `review_summary`（时序只增不改）
- review 明细用 `INSERT OR IGNORE` + `yotpo:{id}` 唯一键去重，增量安全

## 探测顺序（Yotpo 站的正确路径）

1. **先拦截 PDP 网络请求**（BrowserFetcher `bf._ensure()+bf._ctx.new_page()` + `pg.on("response")`）
   看是否出现 `yotpo.com` 域名 → 判定 Yotpo 后端
2. 从 `staticw2.yotpo.com/{app_key}/widget.js` 取 app_key
3. 用 DB `product.sku` 作为 product_id，纯 HTTP 直连 v1 API 取评价
4. 不要先试 VTEX GraphQL reviewsByProductId（Yotpo 站会 400）

## 参考脚本

- 临时脚本模式：`_elektra_gt_rv6.py`（Yotpo 直连 + review/summary 写入 + retail_task_status 留痕）

## 已验证站点

| code | app_key 前缀 | 货币 | 60产品评价情况 |
|---|---|---|---|
| elektra_gt | Yx5PqPQGSeQz... | GTQ | 2/60 有评价（TV支架各1条），58 零评价属实 |
