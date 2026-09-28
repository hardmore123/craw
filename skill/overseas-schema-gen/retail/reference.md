# 判断经验库（零售线 RetailSpec 生成）

> 每条都来自真实零售站的实测教训，不是推测。新增经验请注明来源站点。
> 零售线的红线是**匹配准确率**（防张冠李戴），不是找全率——所以大量经验围绕
> 「怎么确保型号对得上才记」。

---

## 一、搜索页怎么探

### 1.1 搜索关键词必须带品牌 ★

liverpool 实测：裸型号搜索（`s=75U6SV`）会 **302 直达商品详情页**（页面无搜索卡片，
`no_anchor_element` 误判 blocked）；带品牌（`s=Hisense 75U6SV`）返回 56 张候选卡片。

- skill/probe 的 `--model` 应传"真实型号"；搜索关键词由引擎用 `match.search_keyword_template`
  `{brand} {model}` 拼。CLI 跑 `retail --models <型号> --brand <品牌>`。
- 裸型号能直达 PDP 的小站反而可以走「搜索直达详情页」兜底（见 §四）。

### 1.2 候选容器看「型号纯度」不是命中数

liverpool 搜索页：`a[data-testid$='-card-card-link']` 命中 56，但只有 1 个含目标型号
（`model_hit=1, purity=0.018`）。纯子串会撞相近型号（55U6SV/55U7SFM/55UR8SG）。
探测报告 `purity` < 0.3 时，匹配必须交给 `match_product` 的词边界规则，
不要靠"候选里前几个"。

### 1.3 搜索结果会泛化

搜 `Hisense U6N`（系列名）→ 返回 U6SV/U7SFM/UR8SG 等相近型号。**系列名 vs 具体尺寸型号**
（`U6N` vs `55U6N`）是零售线的常见结构，靠 `match.verify.allow_core_match` 处理
（`A65NV → 50A65NV`），但要小心尾缀（`U8Q` 不能撞 `U8QG`）。

---

## 二、搜索直达详情页（单结果 302）

### 2.1 识别

`crawl_model_on_site` 打开搜索 URL 后 `res.url` / canonical 是 `/pdp/...`，页面无搜索卡片，
`open_dom` 的 anchor 判定可能误报 `no_anchor_element` blocked。代码已有 `_search_landed_pdp`
兜底：从 URL / canonical / og:url 提取规范 PDP 并构造单候选继续走匹配。

### 2.2 怎么办

- `search.container` 用「可选的":not()"排除」不必严格：直达场景由兜底函数接管。
- 这种站 **search.container** 优先选 `a[href*='/pdp/']`——如页面有卡片它能命中，
  无卡片则兜底路径接管。

---

## 三、短型号误命中（红线级，务必复现）

### 3.1 `a 4K` 与 `A4K`

liverpool 一个 Hisense 43" 商品标题含 "Accesorios **a 4K**"（AI 提升至 4K），
归一化（去空格大写）后变成 `A4K`——**裸子串匹配直接命中**，把 43S5QFM 当成了 A4K。

修复：`match_product` 精确命中改用**词边界正则**：
```
(?:^|[^a-z0-9]|\d{2,3})<model-escaped>(?=[^a-z0-9]|$)
```
- 前面允许 2~3 位尺寸数字（系列名→带尺寸型号，如 `A65NV→50A65NV`）
- 后面必须词边界（防 `A4K` 撞 `a 4K`）
- 主干匹配仍用归一化子串（已去尺寸/尾缀，误中面小）

**正例要保住**：`75U6SV` 精确、`55QD8SF-PRO → QD8SF` 主干、`A65NV → 50A65NV` 尺寸前缀。

### 3.2 系列名 vs 具体型号（尾缀防撞）

`U8Q`（系列）搜索返回 `100U8QG` 等带尾缀型号——`core_strip_suffix: ["pro","max"]`
只去掉 pro/max，`U8QG` 的 G 不剥。此时 `allow_core_match` 也要谨慎：够短（<4）的型号
不该进主干匹配（min_core_len 保护）。

---

## 四、商品页选择器与兜底

### 4.1 价格区

liverpool：`[data-testid$='-configurator-price']` + `transform: liverpool_money`
（识别 `$12,999.00`）。coppel：`[data-testid='pdp_discounted_price']`。
多数站可加 **JSON-LD 兜底**（`jsonld: "offers.price"`），Amazon/Coppel 常见。

### 4.2 尺寸

- 规格表 kv：`tc-normalizedsize` / `tamaño en pulgadas` 等键 **不是每页都在**。
- **务必给 `product.size` 加 h1 标题兜底**：
  `{"selectors": ["h1"], "kv": {...规格表...}, "transform": "size_inch"}`
  `size_inch` 能从 "65 pulgadas" / "75 Inch" 提取 `65"`。
- 导出列「尺寸」空了先查这个——多半是规格表 kv 没命中、标题兜底没加。

### 4.3 评价区是懒加载

评价区常在商品页底部（PowerReviews/Bazaarvoice），需要浏览器滚动触发。
`fetch.requires_browser: true` + `collect_detail` 的滚动（detail_scroll_passes 默认 14）。
HTTP 直抓 lxml 时评价区常为 0——探测时用 BrowserFetcher，别拿 HttpFetcher 验证评价。

---

## 五、多店铺（聚合站）与导出

### 5.1 kakaku 型（multi_shop）

商品页有「店铺名 → 价格」报价表（`p-priceList_row`）。探测都 `shops.mode`：
```
p-price(list|table|_row)|店名
```
命中 → `multi_shop`（spec 写 shops.list + shops.columns + lowest）；
不命中 → `single_shop`（渠道即店铺，导出简化列）。

### 5.2 导出模板对齐日本线

价格表 = `price_export`（区域|品牌|机型|尺寸|上市|货币|最低价 + 店铺列 + 溯源尾 + 品牌分组行）。
**不要自造 MX 12 列**——用 `rows_to_price_records` + `write_mx_price_*`（内部走 price_export）。
店铺列按 spec 动态（single_shop → 渠道/现价；multi_shop → columns[key]）。

---

## 六、验收流程（每接一站照跑）

```
1) probe.py --site X --model <真实型号> --base-url <URL>   # 探测报告
2) 写 schema_drafts/<code>.retail.json（六段+新段）
3) probe.py --verify data/schema_drafts/<code>.retail.json # 静态校验
4) retail-health --spec <draft> [--models-file]            # 真实站小样：选择器命中+填充率
5) mx_line.py retail --site X --models-file <型号> --spec <draft> --save-db  # 全量+落库+导出
6) 对照 cases.jsonl 沉淀
```

**红线**：`on_no_match` ≠ `skip` 直接判失败；匹配准确率抽检零误匹配才可交付。
---

## 七、URL 兜底匹配（本会话新增 ★）

### 7.1 标题不含型号但 URL 含

amazon_mx 标题是西语"Hisense televisión U6S"不含"75U6SV"，
canadiantire_ca 标题"Hisense Hi-QLED HD Smart VIDAA TV"不含"32QD4SV"，
但 **URL 解码后含完整型号**（`...-32-qd4sv-0436032p.html`）。

- 修复：`match_product` 的 `exact_contains`/`core_contains` 加了 **url 字段**检查
  （`getattr(c, 'url', '')` 加入匹配文本）
- `crawl_ca_retail._payload_matches_model` 也加了 `payload.product.url` 兜底
- spec 配置：`require_model_in: ["title"]`（不强制 sku，因为 sku 可能是 product code）

### 7.2 搜索页 JS 渲染延迟

costco_ca 搜索页需 **5+秒** 才渲染出 `.product.` 链接（crawl_ca_retail 默认 0 秒等待）。
costco_us 需 **scroll=20 + 10秒** 才出 29 个链接（默认 scroll=3 不够）。

- 修复：`crawl_ca_retail._crawl_one` 搜索加 `extra_wait_ms` 从 `spec.search.wait_ms` 读
- spec 配置：`"search": {"wait_ms": 5000}`（costco_ca）/ `10000`（costco_us）
- **新站探测时先手动确认搜索页渲染需要多少秒**，再写进 spec

---

## 八、反爬识别与处理（本会话新增）

### 8.1 Shopify 站 captcha 误判

leons_ca/thebrick_ca 正常页面含 `captcha-bootstrap` 脚本（Shopify reCAPTCHA），
BlockDetector 误判拦截。修复：adapter 加 `block_marker_allowlist = ("captcha",)`。

### 8.2 walmart_us "Robot or human?"

walmart_us 返回 200 但页面是 `Robot or human?` 反爬页。
BlockDetector 补标记 `"robot or human"`, `"bot-message"`, `"activate and hold"`。

### 8.3 liverpool 410 限流

连续抓 200+ 型号触发 410 Gone（IP 被临时拉黑）。
- 间隔 5→12 秒 + 410/403/429 纳入站级熔断（连续 5 个即停）
- IP 冷却后自动恢复

### 8.4 Amazon 独立评价页风控

`/product-reviews/<ASIN>?pageNumber=N` 对代理/无会话返回空壳（无 data-hook=review）。
- 商品页首屏可抽 8 条评价
- 抓全量评价需「CR-widget API 捕获」方案（后续）

### 8.5 Amazon 同系列跨尺寸产品共享评价 ★（2026-09-24 amazon_fr 实测）

amazon_fr（与 ca/us/mx 同构）商品页首屏 `data-hook="review"` 块的 `id`（R 开头
Amazon review ID，如 `R9QU5VHMX9U7X`）是稳定 review_key，**绝不要拼标题+作者伪 key**
（增量去重会失效）。

实测 LG NU80 系列：55NU80（pid=3552）与 65NU80（pid=3559）两个不同 ASIN 的产品页
首屏展示**完全相同的 12 条评价**（review_key/title/author 全一致）——Amazon 把同系列
评价共享给各尺寸产品页，与 §10.4 Costco BV 按系列聚合**同型**。

- `UNIQUE(product_id, review_key)` 允许跨产品重复（product_id 不同），数据真实但
  归属多个产品。导出/分析时需知悉：同一评价会出现在同系列多个尺寸产品下。
- 复用 `overseas/sites/amazon_common.parse_amazon_product` 即可抽评价+汇总
  （avg=`#acrPopover [data-hook='rating-out-of-text']`，total=`#acrCustomerReviewText`），
  无需为新 Amazon 站写 adapter。新模板用驼峰 `reviewTitle`/`reviewText`，旧
  `review-title`/`review-body` 在新 DOM 恒空，parse_amazon_product 已兼容两者。
- 无评价/超时留空不造假（缺价>错价同理）。


---

## 九、crawl_ca_retail 接 spec 匹配（本会话新增）

之前 `crawl_ca_retail._crawl_one` 不走 spec 的 `match_product`，
用 `adapter.parse_search` + `find_product` + `_payload_matches_model`。

修复：有 `retail_spec` 时改走 `_parse_search_with_spec` + `match_product`，
让配置化匹配（含 reject/尺寸校验/URL兜底）覆盖 CA 线。

验证：amazon_ca $199.99、canadiantire_ca $206.79、costco_ca $844.99 均通过。

---

## 十、Costco（CA/US）实测：型号被拆开写 + 评价在接口里（2026-09-20 新增）

> 完整报告：`docs/Costco_CA_US_抓取可行性报告_2026-09-20.md`。
> 本节只留「换个站也会再遇到」的通用经验。

### 10.1 ★ 标题把尺寸和系列拆开写 → 尺寸守卫会静默失效

Costco 标题是 `Hisense 65" Class - U7SG Series`，**没有 `65U7SG` 这个串**。
原尺寸守卫用 `(\d{2,3})<core>` 紧贴匹配，中间夹了 `Class - ` 就永远匹配不到尺寸，
守卫等于不存在 → 65 吋会命中 75 吋的同系列商品。

配置化修法（`match.verify.core_size_tokens`）：

```json
"core_size_tokens": ["class", "inch", "in", "\"", "series"]
```

`_candidate_sizes()` 同时认「紧贴式 / 分隔式 / 无空格式」。**不配这个 key 行为不变**，
所以其它站零影响。会写「NN Class」「NN-inch」「NN"」的站都该配上。

### 10.2 ★ 商品页自报型号做「精确相等」校验，优先于标题子串

Costco 把真实型号写在两处，且两处可能不一致：

```
页头：Item 8987855 | Model 55U78SG
规格表：<th>Model</th><td>55U7SG</td>
```

两个都收进 `Product.model_candidates`，匹配时**任一精确相等即命中，全不中即拒**，
不再退回宽松的标题子串。只用页头会漏掉 `55U7SG`，只用规格表会漏掉 `55U78SG`。

防「对比/相关商品」串味：页头只取第一个、规格表最多 2 个，且其余候选必须与第一个同尺寸。

### 10.3 ★ 「型号搜不到」有两种，处置相反

| | 现象 | 正确处置 |
|---|---|---|
| A 站上确实没有 | 搜索页正常（22~24 条候选），只是没这个系列/尺寸 | 记 `no_item` |
| B 抓取失败 | 搜索页 0 候选 / 被拦 / 出口 IP 不对 | 记 `failed`，**绝不写 no_item** |

B 是常态不是理论风险。Costco CA 搜索页同一 URL 连跑三次：1.84MB→0 条、
2.43MB→24 条、1.84MB→0 条；**空壳版里连品牌名都不出现**。

闸门配置：

```json
"search": {"batch": {"min_candidates": 15, "retries": 3,
                     "max_search_failures": 2, "force": true,
                     "fallback_per_model": false}}
```

- `min_candidates`：候选数低于下限 → 重试；仍不达标 → 判失败跳过品牌
- `fallback_per_model=false`：批量候选里没有的型号直接 no_item，不再逐型号搜
  （CA 621 型号 / US 405 型号，逐型号搜会跑几小时并把站刷爆）
- 失配要给可核对的原因：`explain_no_match()` →
  `同系列(U7SG)有货但尺寸不符：站上只有 55/75"，目标 65"`

### 10.4 评价明细不在 DOM 里时，找挂件的 JSON 接口（BazaarVoice）

`ol.bv-content-list li.bv-content-item` 恒为 0；点标签、滚动 14 次都没明细。
真接口在挂件自己的域上，**纯 HTTP + 静态 token 就能调**：

```
GET https://apps.bazaarvoice.com/bfd/v1/clients/<client>
    /api-products/cv2/resources/data/reviews.json?filter=productid:<id>&limit=100&offset=0
Headers: Bv-Bfd-Token: <displayCode>,<site>,<locale>    ← 三段都在站点 *-config.js 里
         Origin: https://<站点>                          ← ★缺这个直接 401，不是 403
```

`displayCode` 在 `apps.bazaarvoice.com/deployments/<client>/native_review_form/production/<locale>/swat_reviews-config.js`。

**★ 用 55" 的商品号查，返回的 8 条评价 ProductId 全是 75" 的**——BV 按系列聚合，
评价会串尺寸。必须保留来源商品号（本项目写进 `review_url#bv-src-product=<id>`）
并在状态里报数，否则会被当成该型号的用户评价交出去。

**★ productId 映射各站不同，必须实测变体确认**：
- Costco：productId = URL `.product.<numericId>.html` 的数字 id（DB sku 是 slug 不是 id）
- Canadian Tire：★productId = `<pcode>p`，URL `...-4995798p.html` → BV id `4995798p`
  （pcode `4995798` 不带 `p` 查 BV 返回 0 条；带 `p` 才命中 1270 条）
- Samsung 品牌官网：productId = PDP 隐藏 input `resourceModelCode`（厂商型号如 QE42S90HAEXXU）
- Sony 品牌官网（sony_uk 等 sony-global）：★productId = PDP 的 `data-bv-product-id`（系列 slug 如 `bravia-7-ii`），**不是 SKU**（DB sku 如 k50xr75m2pb.uka 查 BV 返回更少且不同批）。URL 路径段 `bravia-7m2` → BV id `bravia-7-ii`（Mark2 系列在 BV 是 `-ii` 后缀），不能从 URL slug 直接推；必须 Playwright 读 PDP `data-bv-product-id`。`/permalink/product/<slug>` 页不渲染（454B 空页）但 BV id=URL slug 兜底即可。displayCode=`12872_22_0`、deploymentZone=`seu`（部署 URL `deployments/sony-global/seu/production/en_GB/`）。★Sony-global 是全球 BV 客户，评价跨语言/地区聚合：summary 受 `contentlocale` 过滤（en_GB=51，多语言=91，`'all'`=0 会误判无评价别用），reviews.json 的 TotalResults 不受 contentlocale 影响始终返回全球全部（en_GB 51 → 全球 123 条）。策略：summary 用 en_GB 对齐 PDP 显示，reviews 不设 contentlocale 抓全球全部（扩大 review 抓取），review 保留 ContentLocale/OriginalProductName/ProductId 溯源防串尺寸/串地区。

**★ AEM 站的 BV 挂件常不 hydrate**：Canadian Tire PDP 是通用 React 模板，
`productFamily` API 被 Akamai 403 拦 → BV 挂件 `data-status="hidden"` 永不加载
（DOM 0 评价明细、`aggregateRating` 缺）。但 BV 评价走**独立 bfd JSON 接口**可纯 HTTP
直连，不依赖 PDP hydrate。探测 BV 站不能只看 PDP DOM，必须找独立接口并实测 productId 变体。

**★ PDP 被 Cloudflare/边缘拦时，从搜索页 JS chunk 找 BV 配置**（falabella_cl 实测）：
PDP 被数据中心 IP 硬拦 403+JS 挑战 40s 不解，但搜索页 `/search?Ntt=<kw>` 纯 HTTP 200。
Next.js 站的 `_app-*.js` chunk 里搜 `BAZAARVOICE_SCRIPT_URL` 可拿到部署 URL：
`apps.bazaarvoice.com/deployments/<client>/<site>/production/<locale>/bv.js`
（Falabella CL: `Falabella/main_site/production/es_CL/bv.js`）。下载该 `bv.js` 搜
`displayCode` 即得 displayCode（CL=1931），site/main_site 从部署 URL 路径段取。
★Falabella Next.js 数据不在 `__NEXT_DATA__` 而在 `<script type="application/json">`
的 `props.pageProps.results[]`，每产品含 `productId/skuId/totalReviews/rating`（搜索页汇总信号）。
BV productId = DB product.sku（Falabella 站内商品号），直接调 BV API 无需搜索匹配。

**★ BV API 的 brotli 压缩陷阱**：BV BFD API 对 `Accept-Encoding: gzip,deflate,br`
会返回 **brotli** 压缩体，Python `gzip` 库解不了 br → `json.loads` 报 `bad_json`
误判 0 评价。必须 `Accept-Encoding: gzip`（只发 gzip 不发 br），否则 urllib 拿到
brotli 乱码解析失败（falabella_cl 首跑 0 条全是这个原因，改 gzip 后 3870 条全抓）。

### 10.5 混合形态站点：搜索要浏览器，详情页走 HTTP

Costco 搜索页是 Next.js 客户端渲染（静态 HTML 0 个商品链接），但商品页是服务端渲染。
spec 里 `fetch.detail_http: true` 让详情页单独用 HttpFetcher：
单 PDP 从 10~20 秒降到 1~2 秒，且不占浏览器并发。

**搜索页的 `wait_selector` 不要填候选容器**：容器是滚动后才懒加载出来的，
`wait_for_selector` 会先白等 8 秒超时。用 `"a"`（或 body），靠 `scroll_passes` 决定
能拿到多少候选——Costco US 滚 3 次只有 8~9 条，滚 20 次才 24 条。候选不全 =
在售型号被误判成 no_item。

### 10.6 品牌搜索关键词不要用型号清单里的展示名

CA 清单的 `brand_name` 是 `Hisense（加拿大）`，直接拼成 `Hisense（加拿大） tv`
会把中文括号送进搜索框。从 `search_query` 里减掉型号前缀取品牌名最稳。

### 10.7 新站接入前的诊断脚本（本次新增，别再用肉眼猜）

| 脚本 | 回答什么问题 |
|---|---|
| `scripts/diag_retail_search.py` | 搜索页这次到底出了几条候选、容器选择器还灵不灵 |
| `scripts/diag_retail_pdp.py` | 商品页的型号/价格/评分/评价容器各命中几个 |
| `scripts/diag_retail_network.py` | 页面的数据接口在哪（含 `--xhr-only`） |
| `scripts/diag_bv_api.py` | BV 接口能不能通、评价字段结构长什么样 |
| `scripts/diag_bestbuy_us.py` | Best Buy 专项六项体检：出口 IP / 搜索候选 / PDP 型号 / 网评后端 / 价格区 / **"搜不到"复现** |

---

## 十一、Best Buy（CA/US）实测：先看合规再谈技术（2026-09-20 新增）

> 完整方案：`docs/BestBuy美国站_参照加拿大Amazon经验_方案与红线.md`、
> `docs/BestBuy加拿大站_sitemap确定性抓取方案.md`。
> 本节只留「换个站也会再遇到」的通用经验。

### 11.1 ★★ 第一优先级是 robots，不是选择器

**在写任何选择器之前先读 `robots.txt`。** 两家 Best Buy 都**禁抓搜索路径**：

```
bestbuy.ca :  Disallow: /en-ca/search     Disallow: /en-ca/Search
bestbuy.com:  Disallow: */site/searchpage.jsp?st=*
```

而两家都**显式放行商品页**（`Allow: /en-ca/product/`、AI 发现组 `Allow: /site/`）
并公布 sitemap。**这不是巧合，是站点在说"按已知 URL 来取，别用我的检索"。**

- 撞到这种站 → 换 `playbook/sitemap_discovery.md`，不要硬用搜索。
- **把 robots 结论抄进交付文档并由业务方确认**，别埋在技术方案里。
- 附带一个反直觉的好处：绕开搜索后，"某型号搜不到"从**模糊检索问题**
  变成**确定性存在性问题**，误抓面直接消失一大半。

### 11.2 ★ 非美国 IP 是 HTTP 200 的国际选择页，不是 403

bestbuy.com 对非美国出口 IP 返回 **HTTP 200** +
`<title>Best Buy International: Select your Country - Best Buy</title>`，
**同 URL、无重定向**（urlscan 上 DE/CH/CZ 节点扫描一致；也有美国 IP 拿到该页的个例）。

**只判状态码会漏判**，然后把"拿到国际选择页"当成"搜索页选择器失效"，
进而把 405 个型号全判成 `no_item`（静默丢数据）。修法：

- `overseas/infra.py` 的 `_BLOCK_MARKERS` 加 `"best buy international"`
  → 识别为 `blocked`（`marker:best buy international`），站级熔断正确触发
- 搜索 URL 带 `intl=nosplash` 抑制插屏；`adapter.is_country_splash(title, html)` 可显式判定
- **别只靠 IP**：要同时带 `intl=nosplash` 并按**标题**识别

### 11.3 ★「连不上」要区分三种根因，别都算成站点反爬

同一次排查里三种失败长得都像"超时/打不开"，处置完全不同：

| 现象 | 根因 | 处置 |
|---|---|---|
| 302 到 `http://10.18.0.250/disable/disable.htm` | **公司内网准入网关白名单** | 换网络，**与站点无关** |
| `SEC_E_UNTRUSTED_ROOT` / `http_code=000` | 企业根证书**中间人** | 同上 |
| 403 `Access Denied`（正文 297~324 字节，真实浏览器也一样） | 站点边缘按 **IP/ASN 硬拦** | 需该国可通行出口 IP |
| HTTP 200 + 国际选择页 | **IP 归属地不对** | 换美国 IP 且带 `intl=nosplash` |

**拿本机的失败去调站点反爬策略是无效工作。** 先用 `ipinfo.io` 确认出口 IP，
再分层定位。`BlockDetector` 已有 `url过滤`/`/disable/disable.htm` 标记可识别网关页。

### 11.4 ★ `min_core_len` 会静默屏蔽主力系列

`match_product` 里 `len(core) >= verify.min_core_len` 是硬开关。实测：

| 清单 | 主干 ≤3 位的型号 | 后果（沿用 `min_core_len: 4`） |
|---|---|---|
| 美国 405 条 | **38 条**（Hisense `U8N/U7N/U6N` 等主力） | 主干匹配被整条跳过 → 大量误判 `no_item` |
| 加拿大 621 条 | **26 条**（`S7N` 24 条、`UX` 2 条） | 同上 |

处置：Best Buy 两站都用 `min_core_len: 3`。**但降它有前提**——见 §11.5。
主干仅 2 位的型号（`UX`、Samsung `HW`）仍不做主干匹配，判 `no_item` 属预期。

### 11.5 ★ 死 SKU 会 301 到无关商品 → PDP 型号校验不可省

Best Buy 旧形态 `/site/<sku>.p?skuId=` 会 301 到新形态，**但已下架的 SKU 可能 301 到
完全不相关的商品，而不是 404**。

- 后果：把"URL 打开成功且有价格"当成"型号存在"，就会**把别的商品价格记到目标型号头上**，
  比"抓到配件价"更隐蔽。
- 处置：**"重定向成功" ≠ "型号存在"**。唯一有效的是 PDP 上服务端渲染的 `Model:`
  与目标型号**精确相等**（`payload_matches_model()`，权威型号非空时只认精确相等）。
- **强耦合**：`min_core_len: 3` 的正确性依赖这道闸成立。**若 PDP 抽不到权威型号，
  必须把 `min_core_len` 调回 4**，两个参数不要单独改一个。

### 11.6 ★ `search.sku` 绝不能配整页 `regex` 兜底

`_parse_search_with_spec` 用**整页 HTML** 构造 `Extractor`，而卡片是逐个传进去的
（`ex.raw(block, cfg["sku"])`）。`Extractor.raw` 的兜底顺序是
`self_attr → self_text → selectors → **regex（整页）** → jsonld（整页）`。

所以给 `search.sku` 配 `regex`，**每张卡片都会从整页正则出同一个 SKU**，
`seen` 去重后**只剩 1 个候选**——搜索页看起来"有结果"，实际退化成单候选，
后果是大量型号误判 `no_item`，**且没有任何报错**。

- 处置：`search.sku` 只用 `self_attr` + 容器内 `selectors`；要从链接取 SKU 用 `url` 字段。
- 注意与 PDP 的对比：**PDP 一页一个型号，整页 `regex` 是安全的**
  （bestbuy.com 的 `Model:75Q651G` 就这么取）；**搜索页一页多型号，整页 regex 必错**。

### 11.7 ★ 品牌搜索关键词的中文国别后缀（修正 §10.6）

§10.6 说"从 `search_query` 里减掉型号前缀取品牌名最稳"——**这条不够**。
实测两份清单的 `search_query` 形态不同：

```
加拿大: search_query = "Hisense 116UX"            ← 干净，§10.6 的做法有效
美国  : search_query = "Hisense（美国） 100U8QG"   ← 带中文全角括号，减前缀得到 "Hisense（美国）"
```

用脏关键词搜品牌 → 候选池整体失效 → **所有型号判 `no_item`（静默丢数据）**。
且因为加拿大清单恰好干净，**这个坑只在换国家时才暴露**。

处置：`crawl_ca_retail._clean_search_text()` 只删「含中日韩字符的括号段」+
残余 CJK，不动型号里可能有意义的半角括号。**换国家时必须验证搜索关键词是纯 ASCII。**

### 11.8 ★ 归因文本必须自洽，否则等于没有归因

`explain_no_match` 原来只分四类，遇到"主干串太短所以主干匹配被跳过"时
会报成 **"同系列有货但尺寸不符：站上只有 65"，目标 65"** —— 自相矛盾，
还把排查方向指向尺寸。已修：

- 主干短于 `min_core_len` → 明确报"主干串仅 N 位 < min_core_len=M，主干匹配被跳过"
- 尺寸其实一致却仍未命中 → 报"请检查 reject 词表 / 主干长度 / 词边界"，不再谎报尺寸不符

**判定"某种结构性问题"时，先确认诊断文本本身是否说得通。**

### 11.9 网评后端要按证据判，别按注释判

Best Buy 的评价后端**同门两站结论相反**，且都推翻了旧注释：

| 站 | 旧注释/直觉 | 实测证据 |
|---|---|---|
| bestbuy.com | adapter 无 source | urlscan `domain:api.bazaarvoice.com AND page.domain:www.bestbuy.com` **0 命中**，页面出现 bv 域名最后记录 **2022-12** → **已弃用 BV** |
| bestbuy.ca | adapter 注释称"评价走 Bazaarvoice" | **从未实测验证**；姊妹站已弃用，故同样存疑 |

- 处置：**不声明 `reviews.source`**，先跑 `diag_retail_network.py --xhr-only` 定类再填。
  填错 source 的表现是"评价恒 0 条"，不报错。
- 先验证据只能**排除方向**，不能替代现场实测。
- 评价**不需要登录**（bestbuy.com 游客态能看到完整正文/徽章/直方图），
  但**需要美国 IP**（Akamai Bot Manager `ak_bmsc`/`bm_s`/`_abck` + reCAPTCHA）。

### 11.10 价格与型号是**服务端渲染**，可直接读，别急着上浏览器渲染

bestbuy.com PDP 初始 HTML 就有 `Model:75Q651G` 与紧邻的 `SKU:6579448`，
以及 `$499.99Your price for this item is $499.99`；型号还会拼进 `<title>`。

- 好处 1：**闸三（权威型号校验）不需要等 JS** —— 这是 §11.5 能放宽的前提。
- 好处 2：PDP 若是纯 SSR，可加 `fetch.detail_http: true` 把单页从 10~20 秒降到 1~2 秒
  （与 §10.5 同思路）。**先测 SSR 够不够，再决定要不要浏览器。**
- ★静默错数据：bestbuy.com 无 `locDestZip` cookie 时按总部 ZIP 55423 渲染配送/价格，
  价格可能不是用户所在地的——**必须记录并在汇报里标注**。

---

## 十二、VTEX 平台：Catalog API 直采（南美 14 站）

> 来源：oechsle_pe / plazavea_pe / carsa_pe / exito_co / multicenter_bo 等 14 站实测（2026-09-23）

### 12.1 VTEX 站不需要 RetailSpec 契约来抓价格目录 ★

VTEX 平台暴露了**公开 Catalog API**，无需认证，直接返回 JSON 产品列表：
```
GET /api/catalog_system/pub/products/search?ft=televisor&_from=0&_to=49
```
- 搜索页是 JS 动态渲染，纯 HTTP 拿不到产品卡片——但 **API 直通**
- **不要为 VTEX 站写 RetailSpec 契约来抓价格目录**，那是绕远路
- **不需要 Playwright**——API 是纯 JSON，比浏览器快 10 倍
- 14/14 站全部可用，响应 2-9 秒

### 12.2 价格在三层嵌套里 ★坑

价格**不在**产品顶层字段，在：
```
items[0].sellers[0].commertialOffer.Price        # 当前售价
items[0].sellers[0].commertialOffer.ListPrice    # 原价
items[0].sellers[0].commertialOffer.Available     # 是否有货
```
漏看 `sellers` 层会拿到 `undefined`。

### 12.3 搜索词用西语 `televisor` 不用 `TV`

`ft=televisor` 比 `ft=TV` 召回率高 3-5 倍（VTEX 全文搜索对西语匹配更好）。

### 12.4 评价数据不在 Catalog API 里

Catalog API 只给价格/产品信息。**评价需另走商品页**（Bazaarvoice/PowerReviews 接口），
届时仍需为评价写 RetailSpec 契约。推荐两步走：API 拉价格 → 再补评价契约。

### 12.5 不要并发请求同一 VTEX 站

VTEX 有速率限制。**串行 + 0.3s 间隔**安全；并发 8 线程会导致超时。
代理出口带宽有限，14 站串行约 60 秒可跑完。



---

## 十三、Magento 平台：搜索页需 Playwright（南美 9 站）

> 来源：hiraoka_pe / coral_ec / panafoto_pa / raenco_pa / laganga_ec / dismac_bo 等实测（2026-09-23）

### 13.1 Magento 无公开 API，搜索页必须用浏览器 ★

与 VTEX 不同，Magento **没有公开 Catalog API**。搜索页 `/catalogsearch/result/?q=<kw>`
的产品列表是 JS 动态渲染的，纯 HTTP 返回的 HTML 里产品卡片为 0。**必须用 Playwright**
渲染搜索页后才能抽取产品链接。

### 13.2 选择器结构高度统一，可批量复制 ★

Magento 站的 DOM 结构几乎相同，用 `hiraoka_pe.retail.json` 作模板可快速复制：

```
搜索容器: li.product-item a.product.photo.product-item-photo
产品标题: .product-item-link / a.product-item-link
价格:     .price-final .price / .price-box .normal-price .price / .price-amount .price
原价:     .price-box .old-price .price / .old-price .price
JSON-LD:  offers.price / offers.priceCurrency / offers.availability
评价:    [itemprop='review'] / .review-item / .customer-review-content
```

### 13.3 3 站连通性问题

- `espana_ec`：SSL EOF（可能 TLS 版本不兼容或站点临时下线）
- `artefacta_ec`：405 Method Not Allowed（首页禁 GET，可能有 JS 跳转）
- `unicomer_gt`：404（`lacuracaoonline.com` 可能已迁移域名）

这 3 站需先解决连通性再写契约。
---

## 十四、Falabella 集团（Next.js/Cencosud）：需 Playwright 或 __NEXT_DATA__ 解析

> 来源：falabella_cl/co / paris_cl / lider_cl / abc_cl / hites_cl 实测（2026-09-23）

### 14.1 无公开 API，搜索页 JS 渲染 ★

Falabella 集团（Cencosud 旗下）全站基于 Next.js。搜索页 HTTP 200 但返回 2.2MB HTML
里 **0 个产品链接**——产品列表完全由 JS 渲染。与 VTEX（有 API）和 Magento（有 JSON-LD）不同。

两种可行路线：
1. **Playwright 渲染**：打开搜索页，等 JS 渲染后写选择器（推荐，直观）
2. **解析 `__NEXT_DATA__`**：617KB JSON 嵌在 `<script id="__NEXT_DATA__">` 里，
   含产品数据但嵌套很深，需定位 `props.pageProps.products` 或类似路径

### 14.2 搜索路径

| 站点 | 搜索路径 |
|---|---|
| falabella_cl | `/falabella-cl/search?Ntt=<kw>` |
| falabella_co | `/falabella-co/search?Ntt=<kw>` |
| paris_cl | `/search?Ntt=<kw>` |
| lider_cl | `/search?Ntt=<kw>` |
| abc_cl | `/search?Ntt=<kw>` |

### 14.3 sukasa_ec / todohogar_ec 搜索路径待确认

两站 `/search?q=TV` 返回 404，搜索路径需重新探测。

---

## 十五、SPEC 品牌官网 Playwright 过 403 WAF

> 来源：lg_us / sony_ca / bestbuy_ca / amazon_mx 实测（2026-09-23）

### 15.1 Playwright 能过大部分 403 WAF ★

| 站点 | 纯 HTTP | Playwright | 结论 |
|---|---|---|---|
| lg_us | 403 | **200 OK, 57 TV 链接** | ✅ Playwright 过 WAF |
| sony_ca | 403 | **200 OK, 843KB HTML** | ✅ Playwright 过 WAF |
| costco_ca/us | 403 | **200 OK, 搜索页加载** | ✅ Playwright 过 WAF |
| bestbuy_ca | 403 | **403 blocked** | ❌ WAF 仍拦截→改用 sitemap |

**结论：Playwright 能过大部分 403，但 bestbuy_ca 需用 sitemap 方案。**

### 15.2 SPEC 品牌产品链接格式各异 ★

| 品牌 | 产品链接格式 | 提取方式 |
|---|---|---|
| LG US | `/us/tvs/lg-<型号>-<类型>` | 正则 `href="(/us/tvs/lg-[a-z0-9-]+)"` |
| Sony CA | `/tv-video/televisions/all-tvs/p/<型号>` | JSON-LD ItemList |
| BestBuy CA | `/en-ca/product/<slug>/<SKU>` | sitemap XML |

### 15.3 BestBuy sitemap 方案绕开 robots 禁搜索 ★

BestBuy robots.txt 禁搜索路径但放行商品页 + 公开 sitemap。
用 sitemap 建本地商品目录，"型号在不在目录里"是确定性问题——
不用碰被禁的搜索路径。37 个子 sitemap，每页 50000 URL，sitemap1 有 21966 产品。

### 15.4 Amazon MX 搜索页 Playwright 渲染 ★

Amazon MX 纯 HTTP 返回 200 但 0 产品（JS 渲染），Playwright 渲染后：
- `data-component-type="s-search-result"` 标记 48 个搜索结果
- `data-asin="<10位ASIN>"` 作为产品 SKU
- h2 > span 提取标题
- `.a-price .a-offscreen` 提取价格

---

## 十六、lider.cl（Walmart Chile / Next.js）：错误 URL 触发 WAF，真分类在导航树

> 来源：lider_cl 实测（2026-09-23）

### 16.1 任务给定的 URL 可能不存在 / 触发 CAPTCHA ★

lider.cl 任务给的 `https://www.lider.cl/tv-y-video/televisores` → **404 + 标题 "Robot or human?"（AWS WAF CAPTCHA）**。该路径不存在，且 Next.js 404 页带 WAF 验证。

但**超市子域** `super.lider.cl` 无 CAPTCHA（只有食品，无电视）。
且 `www.lider.cl` 的 **`/content/<dept>/<id>` 和 `/browse/<path>` 路径纯 HTTP 200 可达**（Next.js SSR，无 CAPTCHA）。

**教训：Next.js 站被 CAPTCHA 拦时，先确认 URL 是否真实存在**——404 页也带 `__NEXT_DATA__`（含完整导航 departments 树），可从导航结构反查正确分类路径。不要因为一个错误 URL 就放弃整站。

### 16.2 从首页 `__NEXT_DATA__` 反查正确分类路径 ★

首页/404页的 `__NEXT_DATA__.props.pageProps.bootstrapData.header.data.contentLayout.modules[].configs.departments` 含全部部门树。流程：
1. 找 `name=="Tecno"` 的部门 → `clickThrough.value=/content/tecno/66849718`
2. 访问该 `/content/tecno/<id>` 页 → 其 `__NEXT_DATA__` 同样含 `subCategoryGroup`，找 `subCategoryHeading=="TV"`
3. 取 `Revisar todo` 链接 → `/browse/tecno/tv/66849718_44699651`（带下划线分类 ID 路径）

正确分类 URL：`https://www.lider.cl/browse/tecno/tv/66849718_44699651`

### 16.3 产品数据在 `searchResult.itemStacks[].items[]` ★

产品结构（Next.js SSR 已渲染，纯 HTTP 即可拿）：
```
路径: __NEXT_DATA__.props.pageProps.initialData.searchResult.itemStacks[].items[]
字段:
  name          标题（含品牌+尺寸+型号，西语）
  brand         品牌（如 "Samsung"/"TCL"，已标准化）
  usItemId      SKU（14位数字，如 "00880609796468"）
  price         价格 CLP（int，如 359990）★ 直接是数字
  priceInfo     {itemPrice:"$759.990"(原价), linePrice:"$359.990"(售价), listPrice, savings}
  canonicalUrl  /ip/tv/<slug>/<SKU> → 完整 URL = https://www.lider.cl/ip/tv/...
  availabilityStatusV2.value  IN_STOCK / OUT_OF_STOCK
  numberOfReviews / averageRating  评价
分页: ?page=N，每页~47-50，maxPage=22（aggregatedCount=4168 含配件）
```

### 16.4 CLP 价格 + 配件过滤坑 ★

- **CLP 价格**：`price` 字段是 int（无符号无千分位），`priceInfo.linePrice` 是 "$359.990"（带 $ 和 . 千分位）。直接用 int `price` 字段最稳；若用字符串需去 `.` 千分位。
- **配件混在 TV 分类里**：`aggregatedCount=4168` 含大量配件（支架 Soporte/Estante、遥控 Control/Mando、灯带 Cinta RGB/Retroiluminación、流媒体棒 Chromecast/Fire TV Stick、背光、HDMI、解码器等）。必须**严格标题过滤**：
  - 必须有屏幕尺寸（NN" / NN inch / NN pulgadas，15-110 寸）
  - 含 televisor / smart tv / 以尺寸开头
  - 排除配件词表（soporte/estante/rack/control/mando/cinta/retroilumin/chromecast/fire stick/streaming/dongle/decodificador/sintonizador/marco/repuesto 等）
- **型号提取**：canonicalUrl 末段是纯数字 SKU（不能用）；型号在标题里，正则 `\b([A-Z0-9][A-Z0-9\-]{4,20})\b` 过滤描述词（CRYSTAL/VISION/SMART/UHD/AI 等），**必须含字母**（纯数字 SKU 不算型号）。短型号（A6N/P7L/V6D/T6D 仅 3 字母）会被 `{4,20}` 漏掉——可接受。

实测结果：1052 原始条目 → 严格过滤后 102 真电视，100% 有价（CLP $117,990 ~ $1,869,990），brand/size 100% 填充，model 83%。

---

## 十七、kakaku.com 评价结构（review_summary + review 明细）

> 实测 2026-09-24，kakaku_jp（site id=4）。电视产品补充网评。

### 17.1 PDP 内嵌 review_summary（无需单独接口）

PDP `https://kakaku.com/item/<item>/` 的 `<section id="review" class="p-review">` 直接内嵌完整评价汇总：

```
平均分(満足度): <span class="p-star p-star-sizeL p-star-starNN">X.XX</span>  → 取文本 float
总件数: <p class="p-reviewChart_info">集計対象19件 / 総投稿数19件</p>  → 集計対象(\d+)件
   兜底: <p class="p-headline_sub">(19件)</p>
5星分布: 5个 <li class="p-reviewChart_item">，每个含
   <span class="p-reviewChart_num">N</span>      (1-5)
   <span class="p-reviewChart_ratio">NN%</span>  (占比)
   → starN = round(total * pct / 100)，四舍五入差额补到最大比例星级，保证 star_sum == total
```

### 17.2 review 明细页（review.kakaku.com）

`https://review.kakaku.com/review/<item>/Page=N/` 抓结构化评价（レビュー）。选择器沿用旧 `crawl_kakaku_reviews.py`：

```
.reviewBoxWt        评价块容器
p.revEntryCont      正文
.reviewTitle a      标题
p.entryDate         日期
.revRateBox table.total td   评分（取不到时 class="rateN" 兜底）
a[href*='ReviewCD='] 详情链 → review_key = ReviewCD=NNN（站内唯一号，regex ReviewCD=(\d+)）
```

翻页：页底 `href="...?Page=N"` 下一页链接，无总页数；取「Page 值 > 当前页」的最小页推进，到无新评价或 404 停止。

### 17.3 ★404 = 无评价（非页面迁移）

**实测关键**：`review.kakaku.com/review/<item>/` 对无评价商品返回 **404**（页面"お探しのページが見つかりません"），对有评价商品返回 200 含 `.reviewBoxWt`。即 404 = 该商品无结构化评价，PDP 的 `(0件)` 也是同样信号。**不要把 404 当页面迁移去改 URL**——它就是无评价。

### 17.4 review_key 必须用站内稳定 ID

review_key 用 URL 里的 `ReviewCD=NNN`（站内评价唯一号），**不用标题+作者拼伪 key**。取不到 ReviewCD 时才回退正文哈希（旧脚本逻辑）。这与"缺价>错价"同理：无评价留空不造假。

### 17.5 网络与重试

OVERSEAS_PROXY=127.0.0.1:7877 可用；偶发 `net::ERR_CONNECTION_CLOSED` 瞬时错误，goto 需重试 3 次、递增 4s 等待。产品间礼貌等待 4s，翻页间 2s。

---

## 十八、法国站（2026-09-25 探测）

### 18.1 Cdiscount：搜索页 JSON-LD 是类目级，产品靠 DOM 提取

Cdiscount 搜索页（`/search/10/<kw>.html`）200 OK + 1.4MB，但 JSON-LD 里只有**一个
`Product` 对象**，其 `offers` 是 `AggregateOffer`（highPrice/lowPrice），`url` 指向搜索页
本身——这是**类目级聚合**而非单商品。直接按 JSON-LD 提取会把整页当成一个产品。

- 产品锚点稳定：`a[href*="/f-"]`（96 个），锚文本是商品标题（跳过"Sponsorisé"广告卡）
- SKU 从 URL 取：`/f-<catcode>-<skuid>.html` 的 `<skuid>` 段（如 `aaarz63853`）
- 价格在 `[class*='price']` 文本块（styled-components 渲染，class 哈希值不稳定，
  但文本含 `599,00 €\n433,74 €\nAjouter`），用 `dom.texts` 取后正则抽 `N[,\.]NN €`
- 尺寸从标题/URL 抽 `N pouces` 或 `N"`
- PDP 是服务端渲染，详情页可走 JSON-LD `offers.price`

**教训**：搜索页 JSON-LD 是 `AggregateOffer`（有 lowPrice/highPrice 无 price）时，它是
类目级标记，不能当单商品提；必须走 DOM 锚点。`_parse_jsonld_product` 应跳过
`offers.@type == "AggregateOffer"` 且 url 指向搜索页的条目。

### 18.2 fnac_fr / darty_fr：DataDome 反爬，需住宅 IP

两站同源 DataDome 防护：HTTP 403 + 1.5~1.8KB 空壳页，标题仅域名（`fnac.com`/
`darty.com`），含 `datadome` cookie + `cf-async` 脚本 + `captcha` 标记。BrowserFetcher
（stealth + human_like）也过不去——DataDome 按出口 IP/ASN 硬拦，机房/代理 IP 不可达。

- 占位 spec：`status=blocked`、`reviews.source=none`
- 需法国住宅 IP 才能继续校准选择器

### 18.3 ebay_fr：eBay 自有反爬，间歇性 403

`ebay.fr/sch/...` 间歇性返回 403（标题 `Error Page | eBay` + `robots noindex,nofollow`），
部分请求 200。eBay 按 IP/ASN 封，降频或住宅 IP 可能改善；但探测阶段不稳定，记 blocked。

- 占位 spec：`status=blocked`、`reviews.source=none`

### 18.4 搜索页 JSON-LD 提取的通用坑

`extract_jsonld_products` 通用提取器对搜索页会误提类目级 `Product`（Cdiscount 实测）。
判定"类目级 vs 单商品"的硬规则：
1. `offers.@type == "AggregateOffer"` 且无 `price` 字段 → 类目级，跳过
2. `url` 指向搜索页本身（含 `/search`、`/sch/`、`/s?`）→ 跳过
3. 有 `aggregateRating.ratingCount` 很大（>1000）且 `category` 等于 `name` → 类目级

满足任一条跳过，避免把搜索页的聚合标记当成单商品入库。

---

## 十九、法国/意大利 15 站（2026-09-25 探测+抓取）

> 来源：auchan_fr/leclerc_fr/rueducommerce_fr/ldlc_fr/eprice_it 成功入库；
> boulanger_fr partial；but/carrefour/conforama/electrodepot/euronics/trony/sonvideo blocked；
> fnac/darty 已知 DataDome blocked。8 站成功+partial+blocked。

### 19.1 ★ 可达站的结构各异，必须逐站写提取器

法国/意大利站平台碎片化，没有统一结构：

| 站 | 平台 | 产品容器 | 价格 | URL |
|---|---|---|---|---|
| auchan_fr | 自建 | `<article itemtype=schema.org/Product>` | `itemprop=price content=N` | `/pr-CXXXX` |
| ldlc_fr | pdt-item | `<li class=pdt-item>` | `<div class=price>599€<sup>00</sup>` | `/fiche/PBXXXX.html` |
| rueducommerce_fr | pdt-item(同LDLC) | `<li class=pdt-item>` | `new-price`/`old-price` 或嵌套 | `/p/rXXXX.html` |
| leclerc_fr | Svelte/AEM | `data-product-card` article | `mdzNT` 卡价 + `dNdEy` 划线 | `/fp/slug-EAN` |
| eprice_it | 自建 | `ep_prodListing` / `/d-XXXX` | `ep_itemPrice>€ N,NN` | 绝对URL `/d-XXXXX` |

**教训**：通用 DOM 正则提取器对欧洲站 0 命中——每站必须按实际 HTML 写专用提取器。
探测时先 `save HTML` 再 `find price class` 确认结构，不要凭 URL 猜。

### 19.2 ★ pdt-item 平台（LDLC/RDC）价格格式多变

LDLC 与 RueduCommerce 共用 `pdt-item` 列表结构但价格 HTML 不同：
- LDLC：`<div class="price"><div class="price">599€<sup>00</sup></div></div>`（整数€+sup 分位）
- RDC 促销：`<div class="old-price"><span class="sr-only">Ancien prix:</span><span>307,39€</span></div><div class="new-price"><span class="sr-only">Nouveau prix:</span>292,02€</div>`
- RDC 普通：`<div class="price"><div class="price">1&nbsp;128,11€</div></div>`（**&nbsp;= 千分位空格**）

三个坑：
1. **&nbsp; 会被正则 `[0-9]+` 截断**：`1&nbsp;128` → 只取到 `1` → 1€。必须 `blk.replace("&nbsp;"," ")` 再匹配 `[0-9][0-9.\s,]*`。
2. **new-price 里有 sr-only span**：`<span class="sr-only">Nouveau prix :</span>292,02€` → 正则要先跳过 `<span>` 标签再匹配数字，不能假设数字紧跟在 `new-price>` 后面。用 `new-price[^>]*>.*?([0-9]...)\s*€` + `re.S` 跨标签匹配。
3. **old-price = 原价，new-price = 售价**：促销产品 list_price 取 old-price，price 取 new-price。

### 19.3 ★ Leclerc：captcha 配置词误判封锁

`e.leclerc/cat/tv` 200 OK + 889KB HTML（正常产品页），但 HTML 含
`"captcha":{"enabled":true,"siteKey":"6LcR..."}`（reCAPTCHA 表单配置，非挑战页）。
`BlockDetector` 的 `captcha` 标记误判为封锁 → 37 个产品被丢。

**修复**：200 OK + 大页面（>20KB）时，只认硬封锁标记（just a moment/datadome/robot or human 等），
`captcha` 作为配置词不触发封锁判定。与 Shopify `captcha-bootstrap` 同型（§8.1）。
`captcha` 标记只在 404/小页面时触发。

### 19.4 ★ 200 OK 但 0 候选 = URL 错或提取器不匹配

多个站给的 URL 404，正确 URL 需探测：
- boulanger：`/nav/recherche/keyword=` 404 → 首页也 400 Invalid URL → 疑似 WAF
- leclerc：`/recherche?recherche=` 404 → 正确是 `/cat/tv`（从首页导航找）
- rueducommerce：`/nav/recherche/X.html` 404 → 正确是 `/recherche/X`
- eprice：`/search?k=` 404 → 正确是 `/sa/?qs=`（意大利语搜索接口）
- trony：`/catalogsearch/result/?q=` 404 + `cloudflare` 标记 → 被拦
- sonvideo：`/recherche/televiseur` 404 + `cloudflare` 标记 → 被拦

**教训**：200 OK 但 0 候选时，先用首页找正确分类/搜索路径（leclerc 实测从首页导航找到 `/cat/tv`）。
404 + cloudflare 标记同时出现 = 被拦，不是 URL 错。

### 19.5 ★ 搜索页 TV 过滤要宽松（型号模式兜底）

法国站产品标题常不含 "televis/tv" 关键词：
- LDLC："LG 50QNED85B6B"、"Samsung LED TU43U7025F"、"Hisense 50E7S"
- Auchan："QILIVE TV Full HD Q40F261B"

搜索/分类页已是 TV 上下文，**不要强制要求标题含 TV 词**。
改用「排除明显配件（telecomando/support/cable/soundbar）+ 型号模式兜底」：
`\b\d{2,3}[A-Z][A-Z0-9]{2,12}\b` 命中即视为电视型号。
严格过滤会把 46 个真电视砍到 5 个（LDLC 实测教训）。

### 19.6 封锁站清单（法国/意大利，2026-09-25）

| 站 | 封锁类型 | 状态 | 标记 |
|---|---|---|---|
| but_fr | IP 层 403 | 403 + 1.5KB 空壳 | http_403 |
| carrefour_fr | Cloudflare | 403 + "Just a moment" 575KB | http_403 |
| conforama_fr | Cloudflare | 403 + "Just a moment" 6KB | http_403 |
| electrodepot_fr | 404+captcha | 404 + captcha 标记 | marker:captcha |
| fnac_fr | DataDome | 403 + 1.8KB 空壳 | 已知(§18.2) |
| darty_fr | DataDome | 403 + 1.5KB 空壳 | 已知(§18.2) |
| euronics_it | Cloudflare | 403 + "Just a moment" 28KB | http_403 |
| trony_it | Cloudflare | 404 + cloudflare 标记 | marker:cloudflare |
| sonvideo_fr | Cloudflare | 404 + cloudflare 标记 | marker:cloudflare |

DataDome/Cloudflare 按 IP/ASN 硬拦，机房/代理 IP 不可达，需住宅 IP。
标记 blocked 不重试（缺价 > 错价）。

### 19.7 ★ 搜索路径变体（200 但 0 产品常因 URL 形式错）

7 个 SPA 站诊断（2026-09-25）：给的 URL 不一定是站点的真实搜索路径，**先确认正确路径再写选择器**：

| 站 | 给的 URL | 正确 URL | 备注 |
|---|---|---|---|
| ananas_rs | `/pretraga?q=` (404) | `/search?q=` (200, 1.4MB) | Next.js+Algolia |
| domod_ba | `/search?q=` (404) | 无搜索路径, 走 `/shop/televizori-av-oprema` 分类或 `/shop/proizvod/<slug>/<SKU>` PDP | 传统 SSR |
| verkkokauppa_fi | `/search?q=` (404) | `/fi/search?query=` (200, 1.73MB) | 注意 `query=` 不是 `q=` |
| klick_ee | `/search?q=` (200) | 同上, 但重写为 `/search/<kw>` | Klevu |
| nay_sk | `/search?q=` (假 200) | 被 F5 拦 | 见 §19.8 |

**教训**：200 但 0 产品时，**先访问首页找搜索表单 action / 导航分类链接**，别硬用任务给的 URL。
`/search?q=` 与 `/fi/search?query=` 只差前缀和参数名，但前者 404 后者 200——参数名错误等同路径不存在。

### 19.8 ★ WAF 假 200 / 三类 bot interstitial（200 但 0 产品也可能是被拦）

诊断 nay_sk 时遇到：返回 **HTTP 200** 但内容是 bot 挑战页（不是 403），极易误判为"页面正常但提取器不匹配"：

| 类型 | 状态 | 特征标记 | 标识 |
|---|---|---|---|
| Cloudflare | 403 | `title='Just a moment...'` + `DOCTYPE XHTML Strict` + `xml:lang` | kaup24_ee |
| DataDome | 403 | `<style>#cmsg{animation...}</style>` + `var dd={'rt':'c','c'...}` | interdiscount_ch |
| F5/Shape | **假 200** | `/TSPD/?type=25` 链接 + `data:;base64` icon + **空 title** + `no-cache` meta | nay_sk |

**判别顺序**：先看 title——`Just a moment`=Cloudflare；空 title + TSPD=F5 假 200；`interdiscount.ch` 短 title + `#cmsg`=DataDome。
**F5 假 200 最危险**：状态码骗过"200=正常"判断，必须按 title 空 + `/TSPD/` marker 识别为 blocked。
这三类都是 fetcher 级 blocker（IP/ASN 硬拦），headless+stealth+持久 profile 均不过，需住宅 IP——非选择器可解决。

### 19.9 ★ 无 JSON-LD / 无 __NEXT_DATA__ 的 SPA 怎么找产品数据

5 个 200 站里只有 0 个有 JSON-LD 产品、1 个有 __next_f（ananas Algolia）。
SPA 站产品数据常见三个藏身处（按可靠性排序）：

1. **`__next_f` 流式 JSON**（Next.js App Router, ananas_rs）：`self.__next_f.push([...])` 多段拼接成完整 JSON, 产品在 `results[].hits[]`（Algolia InstantSearch）。无 `<script id="__NEXT_DATA__">`。
2. **第三方搜索 SDK 嵌入 DOM**（klick_ee Klevu）：`ku*` 类名 + `onmousedown="klevu_analytics.trackClickedProduct(event,{data:{code,salePrice,name,...}})"`，从 onmousedown 正则提 salePrice/code。
3. **SSR 后的卡片 + data 属性**（verkkokauppa_fi / domod_ba）：`data-product-id` + `data-price` + `<data value='' data-decimals=''>` 直接取净价。

**首选**：`data-price`/`data-price-amount` 属性 = 净小数最可靠（别解析显示文本的千分位/逗号小数）。
verkkokauppa 的 `<data data-price='current' value='399' data-decimals='00'>` → `399.00`（value+decimals 拼）比 `sr_true` 文本 `Hinta 00 €` 干净。

---

## 二十、小样本检验方法论 + 数据质量标准（2026-09-25 新增 ★）

> 来源：8 站零售线 + 5 品牌 SPEC 线小样本检验。价格差异≠错价的判定、数据质量清理标准。

### 20.1 ★ 价格差异 ≠ 错价（验证核心原则）

小样本检验时，DB 快照价格与当前页面价格不一致**不一定**是错价：

| 情况 | 判定 | 依据 |
|---|---|---|
| 差异**同向**（全部上涨或全部下降） | **真实涨价/降价**，非错价 | DB 快照日期早于页面当前价，标题/URL 全匹配 |
| 差异**异向**（有的涨有的降） | 需进一步排查 | 可能是个别产品价格调整，也可能提取器不稳 |
| 差异 > 50% | **疑似错价** | 正常涨降幅一般 <30%，>50% 需检查提取器 |

**实测**：costco_ca 3/3 差异 +11~25% CAD、paris_cl 3/3 差异 +12.5~37.5% CLP，均为真实涨价（DB 快照 09-23，页面当前价），标题/URL 全匹配。falabella_pe 3/3 和 vandenborre_be 3/3 零差异。

**验证脚本必须区分**：
1. 快照日期 vs 当前日期（时间差 > 7 天的差异有意义）
2. 标题是否匹配（匹配=同一产品，差异=价格变动）
3. URL 是否有效（200 OK）
4. 差异方向是否一致

### 20.2 ★ JSON-LD offers.price 是跨零售站 PDP 最稳价格路径

小样本检验 4 站（falabella_pe/vandenborre_be/costco_ca/paris_cl）全部通过 JSON-LD `offers.price`（含 `ProductGroup→hasVariant`）命中。优先级：

```
JSON-LD Product.offers.price  >  JSON-LD ProductGroup.hasVariant[].offers.price  >  CSS 选择器  >  DOM 文本正则
```

- `ProductGroup` 结构（Samsung/Sony 官网常见）：PDP 有多个尺寸变体，价格在 `hasVariant[].offers.price` 数组里，需按尺寸匹配
- 纯 DOM 提取最后兜底（受 styled-components class 哈希变化影响）

### 20.3 ★ digitec_ch 假 200（are-you-robot）

digitec_ch 返回 HTTP 200 但 `FetchResult.blocked=True`，页面含 `are-you-robot` 标记。
**不能按 status=200 判可达**——必须检查 blocked 字段和页面内容。

### 20.4 ★ 数据质量清理标准

入库后应清理以下数据：

| 清理项 | 检测方法 | 处理 |
|---|---|---|
| 空 URL 产品 | `SELECT COUNT(*) FROM product WHERE url IS NULL OR url=''` | DELETE + 关联 price_snapshot/review |
| 空/占位标题 | `title LIKE 'Consulta%'` 或空 | DELETE |
| 非电视产品（支架/配件） | `title LIKE '%jalusta%' OR '%teline%' OR '%stand%' OR '%mount%' OR '%Vogel%'` | DELETE |
| 孤儿评价 | `review WHERE product_id NOT IN (SELECT id FROM product)` | DELETE |
| 分类页误当产品 | `url LIKE '%/cat/%' OR url LIKE '%/search?%'` | DELETE |

**实测**：verkkokauppa_fi 28 个 Vogel's TV 支架、amazon_mx 1 个占位、19 个空 URL、public_gr 2 个分类页 = 共删除 50 产品。

### 20.5 欧洲 SPEC 一国一 spec 规则 ★

欧洲区每个品牌只抓 1 个国家的 spec，其它国家共享：

| 品牌 | 抓取站 | 其它国家 |
|---|---|---|
| Samsung | samsung_uk | FR/IT/ES/NL 等用 uk spec 查价格网评 |
| Sony | sony_uk | 同上 |
| LG | lg_uk | 需住宅代理 |
| TCL | tcl_uk | 同上 |
| Philips | philips_de | 同上 |

欧洲零售站（amazon_nl/coolblue_nl 等）用自己的 spec/adapter 抓价格+网评，不依赖品牌 spec。

### 20.6 ★ 批量探测"有价无评"站的 JSON-LD 网评可抓性 ★

实测 82 个 reviews_source=none/无 reviews 能力的有价站（2026-09-26），用 BrowserFetcher 取 PDP →
解析 `<script type=application/ld+json>` 的 `Product.review` 数组。分类占比：

| 分类 | 数量 | 处置 |
|---|---|---|
| JSON-LD review 数组可抓 | 6 | spec 加 `reviews.source=jsonld` + capabilities 加 `reviews` + 入库 review/review_summary |
| 第三方评价系统（BV/yotpo/feefo） | 15 | 记录但暂不抓（需 API 逆向，见 playbook/thirdparty_review_api.md） |
| 仅 aggregateRating 无 review 数组 | 8 | 留空不造假（"缺价>错价"红线） |
| 无任何评价标记 | 49 | reviews.source=none 正确 |
| 探测被拦（403/超时） | 4 | reviews.source 保持 none，需住宅 IP 复测 |

**可抓 JSON-LD 网评的 6 站**：alternate_de / komplett_se / morele_pl / thebrick_ca / vandenborre_be / verkkokauppa_fi。

**关键经验**：
1. **`reviews.source=jsonld` 的 reviews 段结构**（参考 cdiscount_fr）：`jsonld.review_array="review"` + `field_map` 把 `reviewRating.ratingValue/author.name/description/datePublished` 映射到 review 表列；`fields.review_key` 必填（无 `@id` 时用 `datePublished|author.name` 拼）。
2. **亚马逊地区站（be/nl/se/tr）用 DOM `[data-hook='review']` 不用 JSON-LD review 数组** → 探测判 none 是正确的（它们的 reviews 是 DOM 抽取，不在本批 JSON-LD 探测范围）。
3. **aggregate_only 不造网评**：coolblue_be/nl、power_dk/no/se、kakaku_jp、hepsiburada_tr、rueducommerce_fr 只有 `aggregateRating`（评分+总数）无 `review[]` 正文 → 按红线留空，只可补 review_summary 的 avg_rating/total_count。
4. **MediaMarkt 系（be/ch/it/nl）/leclerc_fr/lg_ec/metro_pe/olimpica_co 用 BazaarVoice** → 页面有 `bvapi.js`/`data-bv-` 标记但评价走独立 bfd JSON 接口，需逆向。
5. **探测脚本工程要点**：`nav_timeout_ms=12000` + `MAX_RETRIES_PER_REQUEST=0`（超时即弃，慢站重试无收益）；每站独立**子进程**跑（subprocess + SITE_TIMEOUT=150s）防 Playwright goto 在代理握手时挂死整批；每站最多 5 PDP、最多 30 review；检测到第三方或 JSON-LD review 即提前 break。


### 20.6 WAF 假 200 全面分类表 ★（2026-09-25 汇总）

| WAF 类型 | HTTP 状态 | 特征标记 | 代表站 |
|---|---|---|---|
| **Cloudflare** | 403 | `title='Just a moment...'` + `cf-turnstile-response` | comfy_ua, czc_cz, 220_lv, kaup24_ee, varle_lt, bol_nl, bol_be |
| **DataDome** | 403 | `#cmsg{animation}` + `var dd={'rt':'c'}` + `captcha-delivery.com` | fnac_fr, darty_fr, interdiscount_ch |
| **F5/Shape** | **假 200** | 空 title + `/TSPD/` + `bobcmn` | nay_sk |
| **PerimeterX** | **假 200** | `/blocked?url=` + `Verify Your Identity` | sams_mx |
| **Akamai** | 403 | `Access Denied` + 极短响应(297~324字节) | coppel_mx, bestbuy_ca |
| **假 200（国家验证）** | 200 | `final_url` 含 `/account-verification` 或 `/select-country` | mercadolibre_pe, mercadolibre_cl, trendyol_tr |
| **假 200（机器人挑战）** | 200 | `are-you-robot` + `blocked=True` | digitec_ch |
| **AWS WAF CAPTCHA** | 200 | `title='Robot or human?'` + `final_url` 含 `/blocked?url=` | walmart_cl (lider.cl) |
| **HTTP 503** | 503 | 全站不可达，170B 空壳 | tottus_pe |
| **SSL EOF** | -1 | 连接失败（TLS 版本不兼容或站点下线） | espana_ec |

**判别顺序**：先看 title → 看 final_url → 看 blocked 字段 → 看 body 长度（<5KB 疑似空壳）。

---

## 二十一、Amazon DE 补价实测（2026-09-25）

> 来源：amazon_de 2 产品补价（B09RQ4C4KX / B09Y9GR78L）

### 21.1 ★ Amazon DE PDP 无 JSON-LD（与 amazon_ca/us/mx 不同）

实测 amazon_de 商品页 **0 个 JSON-LD script 块**——与 amazon_ca/us/mx（有
`Product` JSON-LD）不同。价格必须走 DOM 选择器，不能用 `offers.price`。

- `parse_amazon_product` 的 `pick_price_text` 仍可用（价格区校验拒绝推荐位），
  但当无直接 buybox（"See All Buying Options"）时，18 个 `a-price` 容器的
  `in_region` 全为 False → 价格区校验**全部拒绝** → 返回空串 → 留空。
- 此时真实价格在 **`export-alternative-card-desktop` 对比表**的
  `a-color-price a-text-bold`（结构：`<span> Price</span>...<span class="a-color-price
  a-text-bold">€134.45</span>`），这是多卖家比价表，第一个是主价/最低价。
- **`PRICE_GOOD` 列表不含此区域** → `pick_price_text` 会漏价。补价脚本需
  额外加 `a-color-price a-text-bold` 兜底路径。

### 21.2 ★ Amazon CAPTCHA 对代理 IP 频繁出现

amazon_de 对 `OVERSEAS_PROXY=127.0.0.1:7877`（US Sharktech）频繁返回
**CAPTCHA 验证页**（3528B，`validateCaptcha` 表单 + `api-services-support@amazon.com`
注释 + "Klicke auf die Schaltfläche"）。可点 submit 自动提交，但**常无效**
（提交后仍空页 2005B）。需住宅 IP 或降低频率。

- 识别：`html < 5000B` + title 含 "amazon" + 含 `validateCaptcha` 或
  `api-services-support` → CAPTCHA 页，非真实 PDP。

### 21.3 ★ 缺价 > 错价：页面无 € 价格的产品留空

B09Y9GR78L（LG OLED evo Gallery）页面 **0 个 € 符号**（缺货/不可售），
全页无任何价格元素。按"缺价 > 错价"红线**留空不造假**，不取推荐位配件价
当电视价（与 §4.1 的 32A4NV 事故同型）。

### 21.4 南美 6 站全部 IP/ASN 硬拦（非选择器问题）

13 站 0 产品 7 站中 6 个南美站全部因出口 IP（US Sharktech 数据中心）被硬拦，
与站点结构/选择器无关，需各自国家住宅 IP：

| 站 | 封锁类型 | 标记 | 历史一致 |
|---|---|---|---|
| tottus_pe | HTTP 503 | 全站不可达 | — |
| espana_ec | SSL EOF (-1) | 连接失败 | §13.3 |
| falabella_co | Cloudflare 403 | "Just a moment..." | — |
| ripley_cl | Cloudflare 403 | "Just a moment..." | §16.1/line211 |
| mercadolibre_cl | 假 200 国家验证 | `/gz/account-verification` | §20.6/line35 |
| walmart_cl | AWS WAF CAPTCHA | "Robot or human?" `/blocked?url=` | §16.1 |

---

## 二十二、15 blocked 站重探测确认（2026-09-26）

> 来源：argos_uk/ao_uk/very_uk/jd_uk/joybuy_uk/ebay_de/es/it/uk/
> eprice_it/trony_it/visions_ca/walmart_ca/allegro_pl/interdiscount_ch
> 全部用 BrowserFetcher(human_like=True)+_STEALTH_JS 重探测，确认是否仍不可达。

### 22.1 ★ 15/15 仍 blocked，stealth+human_like 不改变 IP/ASN 硬拦结论

用 BrowserFetcher（stealth `_STEALTH_JS` + `human_like=True` + 持久 profile）
对 15 个 `capabilities=[]` 的 blocked 站重探测，**全部仍被拦**：

| 站 | WAF | HTTP 状态 | HTTP 标题 | Browser 结果 |
|---|---|---|---|---|
| argos_uk | Imperva | 403 | Access Denied | 403 (320B) |
| ao_uk | Cloudflare | 403 | Just a moment... | 403 (6195B) |
| very_uk | Imperva | 403 | Access Denied | 403 (4310B) |
| jd_uk | Imperva | 403 | Restricted Access | 404 (搜索路径 404) |
| joybuy_uk | Verify Identity | 404 | Page Not Found | 404 (含 verify your identity) |
| ebay_de/es/it/uk | eBay 自有 | 403 | Error Page \| eBay | 403 (1831B) |
| eprice_it | Akamai | 403 | Access Denied | 404 (Pagina non trovata) |
| trony_it | Cloudflare→404 | 404 | Not Found | 404 (路径变更) |
| visions_ca | Cloudflare | 403 | Just a moment... | 403 (6309B) |
| walmart_ca | PerimeterX | 0 | (连接失败) | 200 假200+captcha |
| allegro_pl | Cloudflare | 403 | allegro.pl | 403 (1507B) |
| interdiscount_ch | DataDome | 403 | interdiscount.ch | 403 (1505B) |

**结论**：stealth/human_like 只解决"浏览器指纹被识别"问题，**不解决出口 IP/ASN
被识别问题**。机房/数据中心 IP（本项目用 US Sharktech）对所有这些 WAF 都是硬拦。
需**各自国家住宅 IP**才能继续。

### 22.2 ★ eBay 四国（de/es/it/uk）完全一致：1831B 403

eBay 四个站点返回**完全相同**的 403 响应（1831B，标题 "Error Page | eBay"），
说明 eBay 用**统一的 IP/ASN 级封锁策略**，不区分国家。四个站点都不需要单独适配，
需换出口 IP（最好是各国本地住宅 IP）。

### 22.3 ★ interdiscount_ch DataDome 间歇性 200 → 3 次重试确认

interdiscount_ch 首跑一次 BrowserFetcher 拿到 **200 OK + 257KB** 真实搜索页
（标题 "Suchergebnisse - Interdiscount"），但后续 3 次重试全部 **403 DataDome**
（1505B 空壳）。这是 DataDome 的**间歇性放行**——偶尔一个请求过关，但很快被
IP 频率标记封回。**不能凭一次 200 判可达**，需多次确认（≥3 次）才可下结论。

### 22.4 ★ trony_it 从 CF 403 变成 404（站点改版/路径变更）

trony_it 此前探测是 Cloudflare 403（§19.6），重探测变成 **404 Not Found**
（359KB 大页面但标题 "Trony.it - Not Found"）。说明 `/televisori` 搜索路径
已变更或站点改版。**blocked 站的状态会随时间变化**，重探测时要注意状态码
可能从 403 变 404（不一定是变好了，可能是路径变了）。

### 22.5 重探测后 spec 更新策略

仍 blocked 的站保持 `capabilities=[]` 不变，但 `_blocked` 段更新：
- `reprobe_date`：重探测日期
- `block_reason`：最新封锁原因
- `note`：注明需住宅 IP

allegro_pl 和 interdiscount_ch **没有 spec 文件**（只有 adapter），因为它们
从未产出过正式 RetailSpec。如未来可达，需新建 spec。

---

## 二十三、零售线熔断机制：避免 WAF 空转（2026-09-26 新增 ★）

### 问题

零售线 S1/S2/S3/S4 场景在 WAF 硬封时**逐条空转**：即使前 5 个 PDP 全部 403/blocked，
仍然对剩余 N 个产品逐个请求，浪费大量时间和代理流量。
SPEC 线已有 `MAX_CONSECUTIVE_SERIES_FAILURES=5` / `MAX_CONSECUTIVE_MODEL_FAILURES=3` 的熔断机制，
但零售线**没有**。

### 空转场景分类

| 场景 | 表现 | 原因 | 旧逻辑 |
|------|------|------|--------|
| A. 搜索页空转 | 有price能力+product=0 | 搜索页 403/429/511 | 逐关键词搜索，每次都 fail |
| B. PDP 空转 | 有产品+价格=0 | PDP 被 WAF 拦截 | 逐 PDP 请求，每次都 fail |
| C. 无价格空转 | PDP 可达+价格=0 | 品牌展示站不卖货 | 逐 PDP 请求，可达但无价 |
| D. 评价空转 | 有产品+网评=0 | 评价页被拦/BV动态加载 | 逐产品翻评价页 |

### 熔断策略（config.py 新增 4 个阈值）

```python
# 搜索页连续被拦 N 次后跳过整站
MAX_CONSECUTIVE_SEARCH_BLOCKED = 3   # → S2 搜索熔断

# PDP 连续被拦/失败 N 次后跳过剩余产品
MAX_CONSECUTIVE_PDP_BLOCKED = 5      # → S1/S3 PDP 熔断

# PDP 连续无价格 N 次后跳过剩余（防品牌展示站空转）
MAX_CONSECUTIVE_NO_PRICE = 10        # → S1 空转检测

# 评价页连续被拦 N 次后跳过剩余产品
MAX_CONSECUTIVE_REVIEW_BLOCKED = 3   # → S4 评价熔断
```

### 各场景熔断逻辑

**S1 搜索→PDP**：
- PDP 连续 `blocked`/`failed` ≥ `MAX_CONSECUTIVE_PDP_BLOCKED`(5) → `break` 跳过剩余
- PDP 可达但连续无价格 ≥ `MAX_CONSECUTIVE_NO_PRICE`(10) → `break`（品牌展示站）
- 成功一次就重置计数

**S2 型号监控**：
- 搜索页连续 `blocked` ≥ `MAX_CONSECUTIVE_SEARCH_BLOCKED`(3) → `break` 跳过剩余型号
- 搜索成功一次就重置计数

**S3 详情直采**：
- PDP 连续 `blocked`/`failed` ≥ `MAX_CONSECUTIVE_PDP_BLOCKED`(5) → `break` 跳过剩余
- 成功一次就重置计数

**S4 评价增量**：
- 评价页连续 `blocked` ≥ `MAX_CONSECUTIVE_REVIEW_BLOCKED`(3) → `break` 跳过剩余产品
- 评价成功一次就重置计数

### 环境变量覆盖

所有阈值均可通过环境变量覆盖（不加就使用默认值）：
```bash
OVERSEAS_MAX_CONSECUTIVE_SEARCH_BLOCKED=3
OVERSEAS_MAX_CONSECUTIVE_PDP_BLOCKED=5
OVERSEAS_MAX_CONSECUTIVE_NO_PRICE=10
OVERSEAS_MAX_CONSECUTIVE_REVIEW_BLOCKED=3
```

### 效果预估

| 场景 | 旧逻辑耗时 | 新逻辑耗时 | 节省 |
|------|-----------|-----------|------|
| 30 站搜索页全封 | 30 站 × 3 关键词 × 15s = 22 分钟 | 3 次被拦即跳过 × 15s = 45s | **97%** |
| 60 个 PDP 全封 | 60 × 15s = 15 分钟 | 5 次被拦即跳过 × 15s = 75s | **92%** |
| 品牌展示站 60 PDP | 60 × 15s = 15 分钟 | 10 次无价即跳过 × 15s = 150s | **83%** |

### 熔断日志

熔断触发时写入 `crawl_error` 表：
- `kind=circuit_break`：PDP 连续失败熔断
- `kind=no_price_circuit`：连续无价格空转检测
- `kind=circuit_break`（stage=search/reviews）：搜索/评价熔断

可在 `crawl_error` 表中查询熔断记录：
```sql
SELECT site_code, stage, kind, message, created_at
FROM crawl_error WHERE kind IN ('circuit_break', 'no_price_circuit')
ORDER BY created_at DESC;
```

---

## 二十、LangGraph并行RETAIL + DeepResearch报告（2026-09-28 新增 ★）

### 20.1 RETAIL并行执行要点

- **并行度=3**：`ThreadPoolExecutor(max_workers=3)` + `as_completed`，每站独立`subprocess.run`避免SQLite线程冲突
- **超时90秒/站**：单站超时不阻塞其他站，`as_completed`实时输出完成站结果
- **子进程隔离**：每站独立子进程+独立DB连接，不用threading
- **配置**：`state['parallel_workers']=3`可动态调整

### 20.2 新站探索后的报告自动生成

探索+抓取完成后，`python -m overseas.report.generate_report`自动生成调研报告：
- 6-Agent协作，每条数据标注信源ID `[来源:Sx]`
- CriticMaster对抗审核检测无数据支撑的结论（幻觉）
- 报告9章：市场总览→数据分析→对手分析→未来报告→上市预警→活动预警→数据缺口→信源索引

### 20.3 新站探索时的RETAIL适配器检查清单

每次新RETAIL站接入完成，**必须**：
- [ ] `retail/cases.jsonl` 追加一条
- [ ] `retail/reference.md` 有新规律/坑则更新
- [ ] 有新结构型 → `retail/playbook/` 加套路
- [ ] `retail/selfcheck.py` 跑一次
- [ ] 爬取进度.csv 更新状态+DB数据
- [ ] 检查country字段是否英文（中文会导致LangGraph区域分类失效）

### 20.4 后续抓取FCC信息（计划）

FCC认证数据可提前2-6月预警新品上市：
- 数据源：`https://fccid.io/` 按品牌型号搜索
- 抓取内容：FCC ID、认证日期、认证类型、内部照片、技术规格书
- 预期增量：每周约20-50条新FCC认证（电视品类）
- 接入方式：新建`overseas/fcc/`模块，作为SPEC分支的补充数据源


