# 套路：sitemap 确定性发现（站点禁抓搜索 / 搜索不可靠）

> 代表站：**bestbuy.ca**（已实测）、**bestbuy.com**（部分证据）。
> 适用前提：站点**公开 sitemap** 且 robots **放行商品页**。

## 一、什么时候该换到这条套路

出现下面任一信号，就**不要**再走"搜品牌 → 候选匹配"的主路径：

| 信号 | 怎么看 |
|---|---|
| **robots 明确禁抓搜索路径** | 读 `robots.txt`：`Disallow: /en-ca/search`（bestbuy.ca）、`Disallow: */site/searchpage.jsp?st=*`（bestbuy.com）。**这是合规问题，不是技术问题，优先级最高** |
| 搜索是广召回 + 广告混排 | 实测 `st=samsung` 返回 22401 条、`Sponsored` 夹在结果中间、`Related Searches` 条、跨零售商块；查询词还会**原样回显**在标题里 |
| 站点有公开 sitemap | `Sitemap:` 行。bestbuy.ca 是 `sitemap_index.xml` → 38 个子图 |
| robots 显式放行商品页 | `Allow: /en-ca/product/`、`/en-ca/category/`、`/en-ca/brand/` |

**关键判断**：如果 robots 放行商品页、却禁抓搜索页，那站点其实在说
**"你可以按已知 URL 来取，但不要用我的检索"**。sitemap 就是把"已知 URL"给你。

## 二、三步流水线

```powershell
# ① sitemap → 本地商品目录（可断点续传，反复跑只补缺）
py -3.12 scripts/bestbuy_ca_catalog.py build --site bestbuy_ca
#    → data/_sitemap/<site>/products.csv   (sku, slug, url, sitemap)
#    → data/_sitemap/<site>/done.json      (已索引子图，续传依据)

# ② 型号清单 → SKU + PDP URL（确定性映射）
py -3.12 scripts/bestbuy_ca_catalog.py match --site bestbuy_ca [--brand hisense_ca]
#    → data/<国家中文名>/bestbuy_ca_match_<brand>.csv
#      (brand_code, brand_name, model, status=found|not_found, hit_count, sku, url, all_skus)

# ③ 直连 PDP 抓价格 + 网评（不搜索）
py -3.12 scripts/crawl_bestbuy_direct.py --site bestbuy_ca `
    --match-csv data/加拿大/bestbuy_ca_match_all.csv --limit 5
```

三条命令对站点只做两件事：**下载 sitemap**（robots 未禁）与**打开商品页**（robots 放行）。

## 三、slug ↔ 型号匹配：三条规则

商品 URL 是 `/en-ca/product/<slug>/<sku>`，slug 是连字符分词的商品名。

**❌ 不要用子串包含**：`U8N` 会命中 `XU8NX` 之类伪命中——与
"`search.sku` 不能配整页 regex"是同一类错误，一处放宽全盘污染。

**✅ 用「分词拼接、允许跳过连接词、完全相等」**（`_slug_matches_model`）：

1. 相邻分词拼接后**完全相等**：`...,65,u8n,...` → `65U8N` ✓
2. 尺寸不同即不同型号：`...,75,u8n,...` **不**命中 `65U8N` ✓
3. 允许跳过**封闭词表**里的连接词（`inch/inches/class/series/tv/uhd/4k/hdr/smart/...`），
   每窗口上限 5 个：`...,65,inch,class,4k,uhd,u8n,...` → `65U8N` ✓
   —— 精度靠**词表封闭**，不靠窗口宽度。

**已知边界（写进交付说明，别当 bug 修）**：若型号本身写成**系列名**（`U8N`），
它会同时命中 65/75 各尺寸。当前加拿大清单里不会发生（实测 621 条中归一化长度 ≤4 的为 **0** 条）。
**若将来清单引入系列名，必须先定义取价口径。**

**性能**：`build` 产出的目录可达 **几十万~百万条**，逐全表跑精确匹配是
O(型号 × 商品)，实测 26 万 SKU × 140 型号 10 分钟跑不完。必须先做
**廉价预筛**（型号主干在归一化 slug 里的子串判断，只对通过的少数候选跑精确匹配）。

## 四、为什么这条路径比搜索更强

| 维度 | 搜索路径 | sitemap 路径 |
|---|---|---|
| 合规 | 可能直接违规 | 只碰 robots 放行的资源 |
| "型号搜不到" | 模糊检索问题，要分四类归因 | **确定性存在性问题**：不在目录里就是没上架 |
| 误抓 | 靠 reject/尺寸守卫/详情页校验三道闸 | 候选**本来就唯一**，再加 PDP 权威型号校验兜底 |
| 请求量 | 每品牌一次搜索 + 候选校验 | 每型号一次 PDP，无搜索 |
| 死循环 | 需要搜索失败熔断、候选重试 | **流程里没有搜索重试**，只需站级熔断 |

**但闸不能省**：`payload_matches_model()`（PDP 权威型号精确相等）仍必须跑——
见 §五第 2 条。

## 五、陷阱（都踩过）

1. **`min_core_len` 沿用 4 会屏蔽主力系列**：美国清单 405 条里 **38 条**主干 ≤3 位
   （Hisense `U8N/U7N/U6N`），加拿大 621 条里 **26 条**（`S7N`、`UX`）。设 4 会让这些
   型号的主干匹配被**整条跳过** → 大量误判 `no_item`。两站都设 `min_core_len: 3`。
2. **PDP 型号校验是放宽 `min_core_len` 的前提**：候选匹配放宽只会多开一个 PDP，
   但**已下架的旧 URL 可能 301 到完全无关的商品**（Best Buy 实测特征）。
   "重定向成功" ≠ "型号存在"——必须靠 PDP 上服务端渲染的 `Model:` 精确相等来确认身份。
   **若 PDP 抽不到权威型号，就把 `min_core_len` 调回 4**（两个参数强耦合）。
3. **sitemap 能下 ≠ 页面能抓**：bestbuy.ca 的 robots/sitemap 全部 200，
   但 `/en-ca/product/...` **连真实浏览器也是 403**（边缘按 IP/ASN 硬拦）。
   目录建设（不需要页面）与抓取（需要页面）是**两件可分开交付的事**——
   前者可在没有代理时先做，且直接回答业务方"这些型号到底上没上架"。
4. **目录比对的漏判方向是安全的**：slug 里不含型号码时会判 `not_found`（假阴性）。
   应改进匹配规则（补"系列名+尺寸"组合），**绝不放宽成子串匹配**。

## 六、验收

```
✓ build 完成度：done.json 子图数 / 索引里的子图总数（★必须报出来，32/38 不能当 38/38）
✓ match 覆盖率：found / (found + not_found)，并抽检 20 个 not_found 确认产品确实不在售
✓ 抓取侧：抽检零误匹配（每个价格都能用 item_id 回到 PDP 复核）
✓ 合规留痕：robots 相关行抄进交付文档，并由业务方确认抓取范围
```

## 相关

- 判断经验：`reference.md` §十一
- 自营站通用段：`single_shop.md`
- 页面拿不到时的止损判定：`needs_human.md`
