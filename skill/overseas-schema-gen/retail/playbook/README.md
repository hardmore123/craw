# 零售站结构型 playbook 索引

按「探测到的结构信号」选择对应套路。若现有套路都套不上 → 新增一份并更新本索引。

## 决策路径

```
Step -1 先读 robots.txt（合规闸，早于一切技术判断）
  │
  ├─ robots 禁抓搜索路径 ────────────────────► sitemap_discovery.md ★优先
  │     └─ 站点公开 sitemap？否 → needs_human.md（无合规入口）
  │
  └─ robots 未禁搜索 → 继续下面的技术判断
        │
        ├─ search 无候选（被拦/直达 PDP/无结果）──► landed_pdp.md / needs_human.md
        │
        ├─ shops.mode = single_shop ─────────────► single_shop.md ★最常见（自营站）
        │     └─ 评价独立页？是 → reviews_page.md
        │     └─ 评价在商品页？是 → 参照 single_shop（含懒加载注意）
        │
        ├─ shops.mode = multi_shop ──────────────► multi_shop.md（kakaku 聚合站）
        │
        ├─ 搜索直达详情页（单结果 302）──────────► landed_pdp.md
        │
        ├─ 候选全 miss 且列表页只有系列名 ────────► series_only_listing.md ★新增
        │     （清单是厂商全码，商品页才写完整型号）
        │
        ├─ 评价容器恒 0 命中、汇总却能拿到 ───────► thirdparty_review_api.md ★新增
        │     （BazaarVoice / PowerReviews 挂件）
        │
        ├─ VTEX 站评价（reviews-and-ratings GraphQL）─► vtex_reviews.md ★新增
        │     （先判定 app 是否安装，再拦截 persisted query 直连公开端点）
        │
        └─ 需要登录 / 验证码 / 加购才显示价 ─────► needs_human.md
```

## 套路清单

| 文件 | 适用结构 | 代表站 |
|---|---|---|
| `sitemap_discovery.md` | **站点禁抓搜索 / 搜索广召回不可靠，但有公开 sitemap** → 用 sitemap 做确定性型号发现，直连 PDP | bestbuy.ca（已实测） |
| `single_shop.md` | 自营站：一个型号一个价，评价随商品页 | liverpool_mx（已验证） |
| `multi_shop.md` | 聚合站：一个商品页列多家店报价（需 shops.columns 声明导出列） | kakaku_jp（已验证） |
| `landed_pdp.md` | 搜索直达详情页（单结果 302 / canonical URL） | liverpool 裸型号场景 |
| `reviews_page.md` | 评价独立页 + 翻页（url_page / query_param / click_more） | Amazon 评价页 |
| `series_only_listing.md` | 列表页只写系列名（`M70H`），清单是厂商全码（`UN55M70HAFXZC`）→ 先解析候选 PDP 的权威型号再精确匹配 | costco_ca / costco_us（已验证） |
| `thirdparty_review_api.md` | 评价明细不在 DOM，走 BazaarVoice 等挂件的 JSON 接口 | costco_ca / costco_us（已验证） |
| `vtex_catalog_api.md` | VTEX 平台价格目录（公开 Catalog API 直采，无需 RetailSpec） | 14 站实测 |
| `vtex_reviews.md` | VTEX 站评价（reviews-and-ratings GraphQL）：先判定 app 是否安装，再拦截 persisted query 直连公开端点 | orvehogar_ec / japon_ec（有评价）、multicenter_bo / plazavea_pe（未装无评价） |
| `yotpo_vtex.md` | VTEX 站但评价走 Yotpo 挂件（非 VTEX 原生，非 BV） | elektra_gt（已验证） |
| `needs_human.md` | 需要登录/验证码/加购才显示价 | Sams / Walmart 部分场景 |

## 红线（任何套路都适用）

- **先读 robots.txt**：禁抓的路径不要走，也不要"先抓了再说"。禁搜索而放行商品页时，
  走 `sitemap_discovery.md`，别硬用搜索
- `match.on_no_match` 必须 `skip`（宁缺毋滥），禁止 `first`
- 匹配准确率 = 100% 抽检零误匹配 才可交付
- 短号型必须词边界匹配（`a 4K` ≠ `A4K`）
- 尺寸列空 = 先查 `product.size` 是否有 h1 标题兜底
- **`no_item` 与 `failed` 严格分开**：搜索页没渲染/被拦是 `failed`，只有"站上确实没有"才是 `no_item`
- **抽样必须包含至少一个该站确实没有的型号**：只验证"能命中"会漏掉误命中，
  两类结果（命中价 / `no_item`）都要看得见原因
- **评价来自第三方接口时先查聚合口径**：BazaarVoice 可能按系列共享评价
  （用 55" 的商品号会返回 75" 的评价），必须保留来源商品号，别当成该型号的用户评价