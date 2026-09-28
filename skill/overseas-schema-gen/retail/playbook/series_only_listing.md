# 列表页只有系列名（series_only_listing）套路

**适用**：搜索列表页/卡片只写**系列名**，不写厂商完整型号；而型号清单里是厂商全码。
**识别信号**：
- `explain_no_match()` 输出「该站没有这个系列（候选 N 条，核心串 XXX 未出现）」，且**整批型号全 miss**；
- 候选标题/URL 里只有 `M70H`、`U7SG`、`QD7` 这类系列串，没有 `UN55M70HAFXZC`、`55U7SG`；
- 但**商品页自己标注了完整型号**（页头 `Model <code>` / 规格表 `Model` 行 / JSON-LD `mpn`）。

代表站：Costco CA/US（实测 Samsung：列表 `Samsung 55" Class - M70H Series`，
清单 `UN55M70HAFXZC`；Hisense 则是 `U7SG` vs `55U7SG`）。

## 为什么候选级匹配必然失败

型号匹配是「拿清单里的型号去撞候选的标题/URL/sku」。列表页没有完整型号时：

| 匹配路径 | 结果 |
|---|---|
| 精确子串（`UN55M70HAFXZC` 是否出现在标题里） | 永不命中 |
| 主干匹配（去尺寸/尾缀后的核心串） | `core_model("UN55M70HAFXZC")` 原样返回（**不是数字开头，剥不掉尺寸**），仍然等于全码 → 也不命中 |

→ 224 个 Samsung 型号全被判 `no_item`，而站上其实有货。**这是假阴性，不是"该站没有"。**

## 解法：先解析候选的权威型号，再精确匹配

spec 开关（`search.batch` 内）：

```jsonc
"search": {
  "batch": {
    "force": true, "fallback_per_model": false,
    "resolve_models": true,      // ★
    "resolve_limit": 40          // 每品牌最多打开多少个候选 PDP
  }
}
```

引擎行为（`crawl_ca_retail._resolve_brand_candidates`）：

1. 品牌搜索拿到候选列表（如 24 条）；
2. **逐个打开候选 PDP**（走 `detail_http` 时是纯 HTTP），抽出 `product.model_candidates`
   （页头 Model + 规格表 Model），顺手把价格与评价也抓好存下来；
3. 建立 `权威型号(归一化) → 已抓好 payload` 映射；
4. 匹配某型号时先查映射（**精确相等**），命中就直接复用，不再发请求。

**安全性**：映射 key 是商品页自报型号的归一化串，匹配是精确相等，没有放宽任何规则。
错的 PDP 只会解出别的型号，进不了映射。所以这一步只影响"打开哪个页面"，
不影响"记谁的数"。

**预算**：上限是**候选数**（~24/品牌），与该品牌的型号数（Samsung CA 224 个）无关，
比"逐型号开 PDP"省一个数量级。

## 前置条件：商品页必须自报型号

先确认（很关键，否则解析出来是空的）：

```powershell
py -3.12 -c "import requests;from overseas.sites.costco_common import extract_models;print(extract_models(requests.get('<PDP>').text))"
```

- 型号藏在**页头**（`Item 8987855 | Model 55U78SG`）与**规格表**（`<th>Model</th><td>55U7SG</td>`）
  两处，且**可能不一致**（系列型号 vs 实际售卖型号）→ 两个都收，任一精确命中即算命中。
- 若商品页也不写完整型号 → 本套路无效，转 `needs_human.md`。

## 配套：尺寸守卫不能因此放宽

解析映射解决的是"找不到页面"，**不是**"可以不做尺寸约束"。两件事都要做：

```jsonc
"match": { "verify": { "core_size_tokens": ["class", "inch", "in", "\"", "series"] } }
```

Costco 的 `65" Class - U7SG Series` 里尺寸和系列被 `Class - ` 隔开，不配这组 token
时紧贴式尺寸守卫 `(\d{2,3})<core>` 永远不命中 → **守卫静默失效** → 65 吋会命中 75 吋。

## 判定清单

- [ ] 整批型号全 miss，且原因都是"该站没有这个系列"
- [ ] 候选标题里只有系列串，没有完整型号
- [ ] 商品页能抽出完整型号（`extract_models` 非空）
- [ ] 抽出的型号能与清单里的型号精确对上（同尺寸、同后缀）
- [ ] `resolve_models` 打开后，抽样型号能命中且价格/评价都拿到

## 验收

- 抽样 3~4 个同系列不同尺寸的型号：应各自命中对应尺寸的 PDP，价格互不相同
- 不存在于该站的型号仍应返回 `no_item`（不能因为开了解析就变成"命中")
- 状态表里 `no_item` 与 `failed` 语义未混淆（搜索没候选必须是 `failed`）
