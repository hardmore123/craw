# 套路：VTEX 站网评探测与抓取（reviews-and-ratings GraphQL）

> 适用站点：VTEX 平台零售站（南美主力：oechsle_pe / plazavea_pe / carsa_pe /
> exito_co / jumbo_co / multicenter_bo / japon_ec / orvehogar_ec / olimpica_co 等）。
> 已验证 2026-09-24：orvehogar_ec / japon_ec（@3.x，有评价）、exito_co（自研 RW 实体）、
> plazavea_pe / multicenter_bo（**未装 app**，无评价）。

## 什么时候用

VTEX 站点的 PDP 评价区由 `vtex.reviews-and-ratings` app 承载时，评价走 GraphQL，
**不要写选择器、不要用 Playwright 渲染评价区**——拦截 persisted query 拿 hash，
再纯 HTTP 直连公开端点取评价明细。

## ★ 第一步：判定 app 是否安装（决定后续路径）

直接 POST 一条直连查询（不带 hash）到公开端点：

```
POST https://{host}/_v/public/graphql/v1?workspace=master&...&locale={LOCALE}&__bindingId={BINDING}
Body: {"operationName":"totalReviewsByProductId",
       "query":"query($productId:String!){totalReviewsByProductId(productId:$productId)}",
       "variables":{"productId":"<DB product.sku>"}}
```

| 响应 | 含义 | 路径 |
|---|---|---|
| **200 + `data.totalReviewsByProductId`** | app 已装，字段存在（@2.x 站如 metro_pe） | 走下面的抓取流程 |
| **400 "GraphQL validation failed"** | app **未装**（schema 无 reviews 字段），或 query 写法与该版本不符（见下） | **无评价，留空不造假**，别再试 hash |
| 400 "Unknown operation named ..." | app **只暴露 persistedQuery 路由**不暴露直连 query（@3.x 站如 carsa_pe/cresa_ec）→ **不等于未装**，需用 persisted query 或 PDP 渲染确认 | 改用 ReviewsByProductId persisted query 探测 |
| 200 + `PersistedQueryNotFound` | hash 未注册（app 装了但版本不同） | 拦截 PDP 拿正确 hash |

**比试已知 hash 更可靠**：hash 会随 app 版本（1.x / 3.x / 自研）变，试错 hash
返 `PERSISTED_QUERY_NOT_FOUND` 分不清"未装"还是"hash 变了"；直连查询返
`GraphQL validation failed` 才是"未装"的确定信号。multicenter_bo 实测：直连
查询 400 validation failed，3.x/1.x hash 全 NOT_FOUND → 未装（不是抓不到）。

> 例外：exito_co 标准组件 total 恒 0 但真实评价在自研 `exito.rating-by-sellers`
> 的 `documents` 持久化查询里（RW 实体）。直连 total=0 不一定是"无评价"——
> 看 PDP 评价区 DOM 有无评价块再下结论。

> ★例外（cresa_ec/cresa_ec_more @ www.crecos.com，2026-09-24 实测）：
> `totalReviewsByProductId` 直连 query 返 **400 "Unknown operation named
> totalReviewsByProductId"**——该站只暴露 persistedQuery 路由，不暴露直连 query。
> 此时直连 query 法失效；改用 **ReviewsByProductId persisted query** 探测：
> 200 + 响应含 `reviewsByProductId` 字段 = app 已装（即使 `range.total=0`）；
> 400 validation failed 或 PERSISTED_QUERY_NOT_FOUND = 未装/版本不符。
> **判"未装"要看 persisted query 是否返 reviewsByProductId 字段，不能只看直连 query 400。**

> ★★写 summary 的红线（cresa_ec_more 踩坑）：`AverageRatingByProductId` 对无评价
> 产品返 `average=0, total=0, stars全0`——这是"真实零评价"不是"有评分"。
> 判据必须用 **`total > 0`** 才写 review_summary，不能用 `avg is not None`
> （0 不是 None 会误判为有评分→写空 summary 造假）。无评价 = 不写 review 不写 summary。

## 关键参数（已装站点）

- **bindingId**：PDP `__RUNTIME__.binding` 或拦截到的 GraphQL URL 的 `__bindingId` 参数（每站固定）。
- **locale**：站点地区，如 `es-BO` / `es-EC` / `es-CO`。
- **productId**：DB `product.sku` 即 VTEX productId（数字如 48586），直接当 GraphQL productId 用，无需转换。
- **公开端点 `/_v/public/graphql/v1`** 用普通 JSON variables（非 base64）；
  PDP 实际请求 `/_v/private/graphql/v1` 且 variables base64 编码，但公开端点纯 HTTP 即可。

## 三条查询

```python
# 评价明细（persisted query，hash 随 app 版本变，需拦截 PDP 拿）
reviews_body = {
  "operationName": "ReviewsByProductId",
  "extensions": {"persistedQuery": {"version": 1,
      "sha256Hash": "<拦截到的 hash>",
      "sender": "vtex.reviews-and-ratings@3.x",       # 版本看拦截到的 sender
      "provider": "vtex.reviews-and-ratings@3.x"}},
  "variables": {"productId": "<sku>", "rating": 0, "locale": "es",
                 "from": 0, "to": 9, "orderBy": "SearchDate:desc",
                 "pastReviews": True, "status": True}}

# 评价总数 + 均分（直连 query，无需 persisted）
query_total = "query($productId:String!){totalReviewsByProductId(productId:$productId)}"
query_avg = ("query($productId:String!){averageRatingByProductId(productId:$productId)"
             "{average starsFive starsFour starsThree starsTwo starsOne total}}")
```

## 拦截 PDP 拿 persisted hash

```python
from overseas.fetchers import BrowserFetcher
bf = BrowserFetcher(proxy=PROXY, headless=True)
bf._ensure()
pg = bf._ctx.new_page()
sha = {}
def on_request(req):
    if "/_v/" in req.url and "graphql" in req.url:
        try:
            body = json.loads(req.post_data)
            ext = body.get("extensions", {}).get("persistedQuery", {})
            if "ReviewsByProductId" in (body.get("operationName") or ""):
                sha["hash"] = ext.get("sha256Hash")
                sha["sender"] = ext.get("sender")
        except Exception: pass
pg.on("request", on_request)
pg.goto(PDP_URL, wait_until="domcontentloaded", timeout=45000)
time.sleep(6)  # 评价区懒加载，等 GraphQL 请求
# sha = {"hash": "3489407e...", "sender": "vtex.reviews-and-ratings@3.x"}
```

## 字段映射 → review / review_summary

```python
review_key     = r["id"]            # UUID，站内稳定，UNIQUE(product_id,review_key) 增量去重
rating         = r["rating"]        # 1-5
title          = r["title"]
body           = r["text"]
author         = r["reviewerName"]
review_date    = r["reviewDateTime"] # 格式 MM/DD/YYYY HH:MM:SS（@3.x）
verified       = 1 if r.get("verifiedPurchaser") else 0
# summary
avg_rating     = avg["average"]
total_count    = avg["total"]
star1..5       = avg["starsOne"]..avg["starsFive"]  # 或 starsFive..One 看 schema
```

## 翻页（from/to 区间）

`from`/`to` 递增到 `>= range.total` 停止；`to-from` 一般 ≤ 49。

## 红线（宁留空不造假）

- app **未装**（直连查询 400 validation failed）→ 不写 review 不写 summary，留空
- `total=0` 且 `average=0` 且星级全 0 → 无评价留空不造假
- 只有 `total > 0` 才写 `review_summary`；明细用 `INSERT OR IGNORE` + UUID 唯一键

## 已验证站点

| code | app 版本 | 端点 | 评价情况 | 备注 |
|---|---|---|---|---|
| metro_pe | @2.13.0 (★直连 inline query, 无 hash) | /_v/public/graphql/v1 | 0/33 全站零 | ★直连query 200(非Unknown operation)；averageRatingByProductId 返回整数非对象无stars分布；PDP纯HTTP只208KB空壳需浏览器渲染确认VTEX IO；全站零评价 |
| orvehogar_ec | @3.x (hash 3489407e) | /_v/public/graphql/v1 | 2/147 有评价 | 直连+普通JSON variables |
| japon_ec | @3.x (同 hash) | /_v/private/graphql/v1 (base64) | 评价少 | private 端点 base64 variables |
| exito_co | 自研 exito.rating-by-sellers | /_v/public/graphql/v1 documents | 走 RW 实体非 ReviewsByProductId | 标准 total 恒 0 是陷阱 |
| plazavea_pe | **未装** | — | 0 | 直连 400 validation failed |
| multicenter_bo | **未装** | — | 0 | 直连 400 validation failed，无第三方挂件 |
| cresa_ec / cresa_ec_more | @3.x (hash 3489407e) | /_v/private + /_v/public | 0/156 + 0/20 全站零 | ★直连 query 返 Unknown operation；用 persisted query 探测；average=0/total=0 不写 summary |
| carsa_pe | @3.20.1 (hash 3489407e / 206747751f) | /_v/public/graphql/v1 | 0/108 全站零 | ★直连 query 返 Unknown operation(只暴露 persistedQuery 路由, 与 cresa_ec 同型)；app 已装但全站零评价；PDP 评价区"Cargando comentarios…" |
| credivargas_pe | @3.20.1 (hash a626a91c / 206747751f) | GET /_v/public/graphql/v1 (base64 extensions.variables) | 0/53 全站零 | ★与 carsa_pe 同集团(Grupo Vargas)同型；GET 端点 + extensions.variables base64 编码 {productId:sku}；productId 用 DB product.sku 非 URL slug reference；ReviewsByProductId 明细 query 返 500 "Multiple app dependencies have defined reviews"(vtex.suggestions-graphql@19.16.0 冲突, 需 @context provider 消歧) 但 total=0 无需查明细 |
| marcimex_ec | @3.20.1 (hash 3489407e) | /_v/private/graphql/v1 (base64) | 0/94 全站零 | private 端点 base64 variables（与 japon_ec 同型）；全站仅8条评论且都不在94产品内；★pastReviews=false 返回全站评论流陷阱（见下） |
| estilos_pe | @3.x (hash a626a91c / 206747751f / 3489407e) | GET /_v/public/graphql/v1 (base64 extensions.variables) | 0/49 全站零 | ★与 credivargas_pe/carsa_pe 同集团(Salas)同型完全一致；GET 端点 + extensions.variables base64；直连 inline query 返 Unknown operation(只暴露 persistedQuery 路由)；PDP 评价区"Cargando comentarios"/"No hay comentarios"；无真实第三方挂件(子串误报需 script[src*=...] 精确计数) |
| jumbo_co | 见 vtex_catalog_api.md（价格） | — | 评价未探 | |

## ★ @2.x vs @3.x 差异（metro_pe 实测，务必保留）

VTEX reviews-and-ratings app 有 @2.x 与 @3.x 两代，**探测与抓取方式不同**：

| 维度 | @2.x (如 metro_pe @2.13.0) | @3.x (如 carsa_pe/cresa_ec/credivargas_pe) |
|---|---|---|
| **请求方式** | 直连 inline query（请求体带 `query` 文本，`extensions`={} 空） | persistedQuery（请求体带 `extensions.persistedQuery.sha256Hash`，无 query 文本） |
| **直连 query 响应** | 200 + data（app 已装且直连可通） | 400 "Unknown operation named ..."（只暴露 persistedQuery 路由，**不等于未装**） |
| **AverageRatingByProductId 返回** | 整数（如 0），**无 stars 分布** | 对象 `{average, starsFive..starsOne, total}` |
| **带 stars 字段查询** | 400 validation failed（@2.x schema 无 stars 字段） | 200 返回对象 |
| **拿 hash 方式** | 无需 hash（直连 query 即可） | 拦截 PDP 请求拿 sha256Hash |
| **ReviewSummary star1..5** | 无 stars 分布，留空 | starsFive..starsOne 映射 |

**判 app 是否装**：不能只看直连 query 响应——metro_pe(@2.x)直连 200=已装，carsa_pe(@3.x)直连 400=也装了(只暴露 persistedQuery)。
**最稳判据**：用 PDP 浏览器渲染看 CSS bundle 是否含 `react~vtex.reviews-and-ratings@<版本>` 组件 + 拦截到 reviews GraphQL 请求。

## ★ pastReviews 陷阱（marcimex_ec 实测，务必保留）

`pastReviews` 参数有两个语义，**写反了会张冠李戴把全站评论当产品评论写入**：

| pastReviews | 返回内容 | 用途 |
|---|---|---|
| **true** | 该 productId 的**专属**评论（range.total 反映该产品真实评论数） | ★抓产品评论用这个 |
| **false** | **全站最新评论流**（一个固定窗口，如最新8条），每条 productId 都 != 查询的 productId | 站点首页"最新评论"展示用，**绝不能当产品评论** |

marcimex_ec 实测：任取三个不同产品 sku，`pastReviews=false` 都返回**完全相同的8条评论**，8条的 productId（24051/17199/25182/...）无一等于查询 sku；`pastReviews=true` 全返 0 → 94 产品真实零评论。

**判据**：写产品评论必须 `pastReviews=true` 且 `range.total > 0`；summary 同理。`pastReviews=false` 返回的条数不代表该产品评论数。

## 参考脚本

- 临时脚本模式：`_<code>_rv8.py`（探测 + 直连 + review/summary 写入 + retail_task_status 留痕）
- `overseas/bv_reviews.py` —— VTEX 站若评价走 BazaarVoice（如 oechsle_pe）用 BV 路径，非本套路
