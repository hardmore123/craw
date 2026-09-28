# 评价内嵌在 __NEXT_DATA__（next_data_inline）套路

**适用**：站点是 Next.js（商品页 `<script id="__NEXT_DATA__">` 内嵌 SSR JSON），
评价**首屏**直接写在 `__NEXT_DATA__.props.pageProps.initialData.data.reviews`
里（聚合 summary + `customerReviews[]` 明细），不需要浏览器渲染、不需要第三方接口。
代表站：lider.cl（Walmart Chile，tenant=CHILE_EA_GLASS，locale=es-CL）。

## 识别信号

- 页面源码有 `<script id="__NEXT_DATA__" ...>`，解析后 `props.pageProps` 巨大（几百 KB）；
- `runtimeConfig.tenant` / `tenantLocale` / `endpointMappings.operations` 指向 Walmart 系（`cegateway`→`/orchestra/graphql`）；
- 评价聚合在 `initialData.data.reviews`：`totalReviewCount` / `roundedAverageOverallRating` / `ratingValue{1..5}Count`；
- 评价明细在 `initialData.data.reviews.customerReviews[]`：`reviewId`(站内稳定) / `rating` / `reviewTitle` / `reviewText` / `userNickname` / `reviewSubmissionTime` / `positiveFeedback` / `negativeFeedback` / `photos` / `media`。

## 两条反直觉红线（lider.cl 实测踩过）

1. **浏览器被硬拦、HTTP 反而通**：lider.cl 上 Playwright（headless / 非headless / 持久化 profile）一律被
   PerimeterX 挑战页 `/blocked`（标题 `Robot or human?`）拦截；而**同一代理**下用 `requests` 直连 PDP
   可稳定拿到 HTTP 200 + 完整 `__NEXT_DATA__`。⇒ 抓评价**不要用浏览器**，直接 HTTP 取 SSR 数据。
2. **速率门槛**：连续快速请求会被边缘返回 ~14KB 的小响应（无 `__NEXT_DATA__`，疑似软拦）。
   实测**串行 + 同域名间隔 ≥6s** 全 102 个产品 0 失败。低于此必出小响应。

## GraphQL 分页不可直连（别浪费时间）

`__NEXT_DATA__` 的 `customerReviews` 受 `limitReviewsToShow` 限制，最多内嵌 ~10 条（即使商品有 6436 条）。
站点评价分页走 `/orchestra/graphql` 的 `ReviewsById` 查询（query 文本在 `_next/static/chunks/pages/_app-*.js` 里可搜到，
hash `f142abb1...`），但**该网关对评价查询返回通用 400 `{"code":400,"message":"Something went wrong..."}`**
（`X-Glass-Routing: gateway`，`fetch-ms=4` 说明在网关层就被拒，疑似需前端 token / 白名单），
即使带 `WM_*` 头、`extensions.persistedQuery.sha256Hash`、PDP cookie 也过不去。
⇒ **不要在 GraphQL 上耗时间**。按红线只采站点首屏给的 summary + 内嵌评价明细，
`totalReviewCount > 内嵌条数` 时在日志明确标注余量，不造假、不补齐。

## 正确做法（HTTP 直连 + 7s 限速）

```python
import re, json, requests, urllib3
urllib3.disable_warnings()
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
P = {"http": "http://127.0.0.1:7877", "https": "http://127.0.0.1:7877"}
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 ... Chrome/126 ..."})
r = s.get(pdp_url, timeout=30, verify=False, proxies=P)   # 不带浏览器
nd = json.loads(NEXT_DATA_RE.search(r.text).group(1))
data = nd["props"]["pageProps"]["initialData"]["data"]
reviews = data["reviews"]                                   # 聚合
cust = reviews.get("customerReviews") or []                # 首屏明细(~10条)
# summary: reviews["roundedAverageOverallRating"] / ["totalReviewCount"] / ["ratingValue{1..5}Count"]
# review_key: cust[i]["reviewId"]
```

- 写 `review_summary`：`roundedAverageOverallRating`→`avg_rating`、`totalReviewCount`→`total_count`、
  `ratingValue{One..Five}Count`→`star{1..5}`。
- 写 `review`：`reviewId`→`review_key`、`rating`→`rating`、`reviewTitle`→`title`、`reviewText`→`body`、
  `userNickname`→`author`、`reviewSubmissionTime`→`review_date`、`positiveFeedback`→`helpful_count`、
  `photos[].sizes.{normal,thumbnail}.url` + `media[].{normalUrl,videoFallbackUrl}`→`image_urls`/`video_urls`。
- **无评价**（`totalReviewCount=0` 且 `customerReviews=[]`）的产品：`review` 与 `review_summary` 一律留空，不造假。

## 何时用这个套路 vs `thirdparty_review_api`

- 评价明细**已经在页面 JSON 里**（Next.js SSR）→ 用本套路（HTTP 直连）。
- 评价明细**不在页面里**、由第三方挂件（BazaarVoice 等）的接口返回 → 用 `thirdparty_review_api`。
- 评价明细要**翻页**且接口可直连 → `thirdparty_review_api`；翻页接口被网关挡住 → 本套路只采首屏 + summary，标注余量。
