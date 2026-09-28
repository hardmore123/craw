# 评价在第三方接口（thirdparty_review_api）套路

**适用**：商品页的评价区由第三方挂件（BazaarVoice / PowerReviews / Yotpo …）渲染，
DOM 里**拿不到评价明细**，但挂件会调一条可直连的 JSON 接口。
**识别信号**：
- `reviews.container` 选择器恒命中 0（如 `ol.bv-content-list li.bv-content-item` = 0）；
- 但汇总能拿到（`4.4` / `(8)`）；
- 页面里有 `data-bv-show` / `data-bv-product-id` / `*.bazaarvoice.com` / `*.powerreviews.com`；
- 点「Member Reviews」标签 + 长滚动后明细**依然是 0**。

代表站：Costco CA/US（BazaarVoice）。

## 别浪费时间的三个动作

1. 加评价选择器 —— 命中 0 不是选择器写错，是 DOM 里根本没有。
2. 点标签 + 滚动 —— 实测点 `Member Reviews` + 滚 14 次，明细仍 0。
3. 用浏览器抓 —— 浏览器里同样不出明细，而接口用**纯 HTTP** 就能调。

## 正确的做法：找挂件自己的接口

### 第 1 步：抓网络请求，定位接口与请求头

```powershell
py -3.12 scripts/diag_retail_network.py --url "<PDP>" --match "bv-bfd,/resources/data/" --wait 6000 --scroll 6
```

输出里能直接看到接口 URL 和它带的自定义头（脚本会打印 `[REQ] ... headers:`）。

### 第 2 步：BazaarVoice 的形态（实测）

```
GET https://apps.bazaarvoice.com/bfd/v1/clients/<client>
    /api-products/cv2/resources/data/reviews.json
    ?apiVersion=5.4&filter=productid:<id>&limit=100&offset=0
    &include=products&contentlocale=<locale>

Headers:
    Bv-Bfd-Token: <displayCode>,<site>,<locale>
    Origin: https://<站点域名>
```

三个要点：

1. **`Bv-Bfd-Token` 是静态可推导的**，格式 `displayCode,site,locale`。
   三个值都在站点自己的 BV 部署配置里：
   `https://apps.bazaarvoice.com/deployments/<client>/native_review_form/production/<locale>/swat_reviews-config.js`
   （`"displayCode"`）与 `api-config.js`（`"client"` / `"site"`）。
   **不用逆向 JS、不用找 passkey。**
2. **`Origin` 是硬要求**：缺了返回 `401 Unauthorized`（不是 403，容易误判成"没权限"）。
3. 汇总另有一条：`.../display/0.2alpha/product/summary?productid=<id>&contentType=reviews,questions
   &reviewDistribution=primaryRating,recommended&rev=0&contentlocale=<locale>`
   → 给 `numReviews` + 星级分布（比 JSON-LD 只有均分更全）。

两站实测取值：

| 站点 | client | displayCode | site | locale |
|---|---|---|---|---|
| costco.ca | `Costco-EN_CA` | `20040_1_0` | `native_review_form` | `en_CA` |
| costco.com | `Costco` | `2070_2_0` | `native_review_form` | `en_US` |

### 第 3 步：写进 spec

```jsonc
"reviews": {
  "source": "bazaarvoice_bfd",
  "bv": {
    "client": "Costco-EN_CA", "display_code": "20040_1_0",
    "site": "native_review_form", "locale": "en_CA",
    "origin": "https://www.costco.ca", "content_locale": "en_CA,en_US,fr_CA",
    "page_size": 100, "max_pages": 3, "max_reviews": 100
  },
  "fields": { "review_key": { "self_attr": "id" } }   // DOM 路径兜底声明，校验要求
},
"paginate": { "reviews": { "type": "bv_api", "max_reviews": 100, "max_pages": 3 } }
```

执行在 `overseas/bv_reviews.py`，由 `crawl_ca_retail._collect_reviews` 分派
（`source == "bazaarvoice_bfd"` 时**不走** `collect_reviews_paged`）。

## ★ 必查：第三方评价可能不按你的商品聚合

**Costco 实测**：用 55" 的商品号 `4201009218` 查，返回 8 条评价，
**8 条的 `ProductId` 全是 75" 的商品号 `4201004539`**（`OriginalProductName` 写着
`Hisense 75" Class - U7SG Series`）。也就是 Costco 把同系列评价共享给每个尺寸。

处置（不许隐藏）：

- 每条评价保留来源商品号：`review_url` 追加 `#bv-src-product=<真实商品号>`
- 状态/汇报里报数：`★8/8 条评价来自同系列其它商品（BV 按系列聚合）`

否则这些评价会被当成"该型号的用户评价"交付出去。

**接入任何第三方评价接口时，先打印前几条的 `ProductId` 和请求的商品号对比一次。**

## 分页安全阀（防死循环）

`fetch_bv_reviews` 内置，五道：

| 闸门 | 触发条件 |
|---|---|
| `max_reviews` | 累计条数达上限 |
| `max_pages` | 页数达上限 |
| `exhausted` | `offset >= TotalResults` |
| `page_all_known` | **本页没有任何新评价**（站点对越界 offset 回首页时立刻退出） |
| `empty_page` | 返回空 `Results` |

## 坑

- **不要用 `res.ok` 判断汇总接口成功**：`BlockDetector` 有"正文过短即可疑"这一路，
  汇总 JSON 只有几百字节会被误标 `blocked`（`too_short`），但它是合法的。
  判据用 `status == 200` + 能解析出 JSON。
- 评价接口在**另一个域名**（`apps.bazaarvoice.com`），与站点白名单/robots 无关，
  但仍应限速并复用缓存。
- `Photos` / `Videos` 结构随 BV 版本变化，取图要防御式写（多 key 兜底）。

## 验收

- [ ] 接口直连能返回 `TotalResults > 0`
- [ ] 抽 2 个不同商品号，确认返回的评价条数/内容确实不同（不是同一批）
- [ ] 打印对比请求商品号与返回的 `ProductId`，跨商品条数已记录
- [ ] 不存在的商品号：应在 1 页内停止，不能翻页翻到天荒地老

---

# Feefo 后端（Hughes UK 实测，2026-09-24）

**适用**：商品页评价区由 Feefo 挂件渲染（`<feefo-product-stars>` / `#feefo-reviews`），
DOM 拿不到明细，但 Feefo 有**公开 JSON API**（不需登录、不需 token、不需 Bv-Bfd-Token）。

代表站：Hughes UK（`hughes.co.uk`）。

## 识别信号

- 页面含 `api.feefo.com/api/javascript/hughes`（feefo-loader）、
  `register.feefo.com/feefo-widget-v2/js/feefo-widget.js`；
- `<feefo-product-stars product-sku="<SKU>">` 自定义元素；
- 评价区 `<div id="feefo-reviews">` 懒加载，DOM 明细为 0。

## 两条接口（纯 HTTP，Origin/Referer 指向站点即可）

```
# 汇总（写 review_summary）
GET https://api.feefo.com/api/10/reviews/summary/product
    ?product_sku=<SKU>&origin=www.<site>.co.uk&merchant_identifier=<m>
    &since_period=ALL&sort=-updated_date&feefo_parameters=include
    &media=include&translate_attributes=exclude
    &reviews_with_content_count=include&importedReviews=true
→ rating.rating = avg, meta.count = total(含空评论),
  rating.product.{1..5}_star = 星级分布, rating.product.count = 评分数

# 明细（写 review，分页）
GET https://api.feefo.com/api/10/reviews/product
    ?product_sku=<SKU>&origin=...&merchant_identifier=<m>&since_period=ALL
    &full_thread=include&unanswered_feedback=include
    &page_size=100&sort=-updated_date&page=<N>
    &feefo_parameters=include&media=include&demographics=include
    &translate_attributes=exclude&empty_reviews=false&importedReviews=true
→ summary.meta.{count,pages,page_size}  reviews[].{
    merchant.identifier, customer.display_name,
    products[].{ id, review, rating.rating, created_at,
                 helpful_votes, feedbackVerificationState,
                 product.{sku,reviews_url,image_url}, media }
  }
```

- `product_sku` = 站内产品 SKU（Hughes 是 `BRANCH-MODEL` 如 `LG-OLED55B56LA`）；
- `merchant_identifier` = Feefo 商户号（Hughes 是 `hughes`），可在 feefo-loader URL
  `api.feefo.com/api/javascript/<merchant>` 里看到；
- `page_size=100`（默认 20，用 100 把 14 页压成 3 页，减 80% 请求）。

## ★ 三条核心经验（Hughes 实测踩坑）

1. **跨商户聚合**：`merchant_identifier` 只标识聚合**入口**，**不过滤**评价——
   返回的评价 `merchant.identifier` 会出现 currys/lg-uk/public-gr 等多家。
   这是 Feefo 的产品级聚合（同型号跨商户评价合并）。`review_key` 用 Feefo 全局唯一
   `products[].id`；溯源（merchant + 来源 product.sku）写进 `review_url` 锚点
   `#feefo-merchant=<m>[&feefo-src-sku=<s>]`。同系列不同尺寸也会共享聚合
   （TCL V6DUK 43/50/55/65/75 五尺寸均 `total=466/reviews=397`）。

2. **代理 SSL 间歇性失败**：`OVERSEAS_PROXY` 上 Feefo 有 5~10% 请求报
   `SSL UNEXPECTED_EOF_WHILE_READING`（中间设备截断），单次请求必丢数据。
   **必须双层重试**：`feefo_get` 内 `RETRIES=4` 指数退避（1.5/3/4.5/6s）+
   分页 `page_retries=3` 整页重试。不加的话 `total=331` 只抓到 100 条（半途中断）。
   重试时**每次开全新 opener**（避免复用坏连接）。

3. **无评价判定不能靠 `avg is not None`**：Feefo 对无评价产品返回
   `rating.rating=0.0`（float，**不是 None**）+ `meta.count=0` + 星级全 0。
   `has_data` 必须以 `total>0` 或 `stars>0` 为准；用 `avg is not None`
   会把无评价当有评价，写 `avg=0.0/total=0` 的**假 summary**（违反缺价>错价红线）。

## summary/reviews 不一致（非造假）

- summary `meta.count`（含空评论）与 reviews API `meta.count`（`empty_reviews=false`
  只含正文）不等：如 331 vs 277（54 条空评论）。
- 个别产品 summary `total=1` 但 reviews API `count=0`：那 1 条是无正文评论，
  Feefo 数据不一致。处置：summary 照写（评分真实）、review 留空，不算造假。

## review 字段映射

| review 列 | Feefo 字段 |
|---|---|
| review_key | `products[].id`（全局唯一） |
| rating | `products[].rating.rating` |
| body | `products[].review` |
| author | `customer.display_name` |
| review_date | `products[].created_at` |
| verified | `feedbackVerificationState == "feefoVerified"` |
| helpful_count | `helpful_votes` |
| review_url | `products[].product.reviews_url` + 溯源锚点 |
| image_urls | `products[].media`（防御式取，多 key 兜底） |

## 验收（Feefo）

- [ ] summary API 对有评价产品返回 `meta.count>0` + `rating.rating>0`
- [ ] 对无评价产品返回 `meta.count=0` 且**不写 summary**（留空不造假）
- [ ] reviews API 分页能取完全部正文评论（`len(reviews) ≈ meta.count`，含内容部分）
- [ ] 抽查 `review_key` 全为 Feefo `products[].id`，无伪造
- [ ] review_url 带 `#feefo-merchant=` 溯源锚点（跨商户评价可追溯）

---

# LFL Group 自建评价后端（Leon's/The Brick CA 实测，2026-09-24）

**适用**：Shopify 站（Leon's、The Brick 同属 LFL Group）的评价区由自建系统承载
（`ecom.api.lflgroup.ca`），非 BazaarVoice/Yotpo/Feefo。PDP 评价在折叠 `<details>` 里，
DOM 默认不渲染明细，但有可直连的 JSON 接口（纯 HTTP，无需浏览器/stealth）。

代表站：Leon's CA（`www.leons.ca`）。

## 识别信号

- PDP 含 `<details class="product-reviews-tab" data-product-reviews-vue-container>`（评价折叠）；
- 页面有 `reviewCount:<N>` 脚本变量（如 `reviewCount:429`）；
- 无 `bazaarvoice`/`yotpo`/`turnto`/`feefo` 引用——是自建系统；
- 浏览器展开 `<details>` 后触发 `POST ecom.api.lflgroup.ca/ecom/item/reviews/get`。

## 两条接口（纯 HTTP）

```
# 明细（写 review，分页）
POST https://ecom.api.lflgroup.ca/ecom/item/reviews/get
Headers:
    Content-Type: application/json
    x-shopify-shop-domain: www.leons.ca   # ★必需，缺则 400 'missing shopify header'
    Origin: https://www.leons.ca
    Referer: https://www.leons.ca/
Body: {"sku":"<内部SKU>","page":<0-based>,"pageSize":100,"sortBy":"dateDesc"}
→ {averageRating, totalReviews, totalReviewsBreakdown:{1..5},
   totalFilteredReviews, reviews:[{id,title,content,rating,
   positiveFeedbackCount,negativeFeedbackCount,author,
   submissionTime(unix秒),location,recommended,origin:{name}}]}

# AI 摘要（可选，不入库）
GET https://ecom.api.lflgroup.ca/ecom/item/reviews/summary?sku=<内部SKU>
→ {"message":"<AI 摘要文本>"}
```

要点：
1. **`x-shopify-shop-domain` 是硬要求**：缺则 400 `{"error":"missing shopify header"}`。
2. **signature 非必需**：浏览器请求 body 带 `product_info.signature`，但直连 HTTP
   不带 signature 也 200 OK——signature 只是浏览器侧的防篡改，服务端不强校验。
3. **内部 SKU ≠ 厂商型号**：API 的 `sku` 是 Shopify 内部 SKU（如 `14600D7Q`），
   不是厂商型号（`100QD7QFM`）。需逐产品 HTTP 取 PDP，从 JSON-LD
   （`<script id="product-jsonld">` 里 `"sku":"..."`）提取。
4. `pageSize=100`（默认 10，用 100 减 10 倍请求）；`page` 0-based，到尾页返回
   `<pageSize` 条自动停。

## review 字段映射

| review 列 | LFL API 字段 |
|---|---|
| review_key | `reviews[].id`（数字 id） |
| rating | `reviews[].rating` |
| title | `reviews[].title` |
| body | `reviews[].content` |
| author | `reviews[].author` |
| review_date | `time.strftime('%Y-%m-%d', gmtime(submissionTime))`（unix 秒 → ISO） |
| helpful_count | `positiveFeedbackCount` |
| verified | 无字段 → None |
| review_url | product_url + `#origin=<origin.name>`（溯源：评价可能来自品牌官网如 hisense-canada.com） |

| summary 列 | LFL API 字段 |
|---|---|
| avg_rating | `averageRating` |
| total_count | `totalReviews` |
| star1..5 | `totalReviewsBreakdown["1".."5"]` |

## ★ 旧 DOM 抓取遗留清理

旧 adapter 用 DOM 抓取只拿到首屏 10 条（`review_key=标题`），大量遗漏。API 全量分页
可达数百条（`review_key=数字 id`）。两套 `review_key` 体系混存会重复，**必须 pre_clean**：
运行前删除该站全部旧 `review` + 旧 `review_summary`，让 API 数据成为唯一真相。

## 无评价判定

- PDP 脚本 `reviewCount:0`（metafield）→ API `totalReviews=0` → 记 `no_reviews` 留空不造假。
- 不可靠判据：`averageRating is not None`（LFL 对无评价返回 `averageRating:0`，类似 Feefo）；
  必须以 `totalReviews>0` 为有评价判据。

## 验收（LFL）

- [ ] 直连 reviews/get 返回 `totalReviews>0` 的产品，`len(reviews)≈totalReviews`（分页取尽）
- [ ] `reviewCount:0`（metafield）的产品记 `no_reviews`，不写 review/summary
- [ ] 抽查 `review_key` 全为数字 id（API `reviews[].id`），无标题伪 key 混存
- [ ] summary 星级分布 `star1..5` 非空（来自 `totalReviewsBreakdown`）
