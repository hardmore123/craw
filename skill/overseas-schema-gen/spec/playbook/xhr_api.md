# 套路：JSON 接口出数据（xhr_api）

> 状态：✅ **引擎已实现**（`discover.mode="xhr_api"`，2026-09-16）
> 目标站点：TCL、Samsung、LG
> 离线等价性已验证（字段映射、family_id 防同名覆盖、接口失败回退、SSRF 拦截）；
> 真实站需联网确认接口可达与鉴权。

---

## 为什么优先用它（数据质量更高）

技术方案设计原则："优先官方接口——比 DOM 稳"。

| | DOM 解析 | JSON 接口 |
|---|---|---|
| 改版影响 | 选择器一改就失效 | 接口字段相对稳定 |
| 找全 | 靠滚动/分页判断"抓完了" | 接口能一次返回全量（`num=500`） |
| 型号提取 | 靠 URL 正则，slug 乱就没戏 | 直接读 `modelCode` 字段 |
| 系列身份 | 靠 URL/名称推 | 直接读 `familyId`，**天然解决同名系列覆盖** |

---

## 识别信号

| 信号 | 说明 |
|---|---|
| 入口页 HTML 几乎无产品链接，但页面肉眼有很多产品 | 典型 SPA，产品由 JS 从接口拉取 |
| `static_unique_links` 极少 | 数据不在初始 HTML |
| 站点有官方 finder / product-list 接口 | Samsung finder 即例证 |

**怎么找接口**：`probe.py` 当前不自动采集 XHR（P0-2 的探测侧增强待做）。
现阶段人工在浏览器 Network 面板筛 XHR/Fetch，找返回产品数组的 JSON 接口，
记下 URL、请求参数、响应里产品列表的路径与字段名。

---

## schema 模板

```json
{
  "spec_version": "1.0",
  "code": "samsung_us",
  "brand_name": "Samsung（美国）",
  "region": "us",
  "base_url": "https://www.samsung.com",
  "entry_url": "https://www.samsung.com/us/televisions-home-theater/tvs/all-tvs/",
  "fetch": { "requires_browser": false, "interval_sec": 6.0, "page_wait": "body" },
  "discover": {
    "mode": "xhr_api",
    "api": {
      "url_template": "https://www.samsung.com/us/api/.../finder?type=tv&num=500",
      "list_path": "response.resultData.productList",
      "model_field": "modelCode",
      "url_field": "pdpUrl",
      "series_field": "familyName",
      "family_id_field": "familyId"
    },
    "fallback_dom": true,
    "link_selector": "a[href*='-sku-']",
    "model_url_regex": "-sku-([a-z0-9]+)"
  },
  "spec": { "extract_order": ["embedded_json", "generic_spec_js"] },
  "identity": { "series_key": "family_id", "model_normalize": "upper_alnum" },
  "expected": { "series_count": null, "model_count": null }
}
```

---

## `discover.api` 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `url_template` | 是 | 接口 URL。**必须与 base_url 同源**（SSRF 校验会拦站外域名） |
| `list_path` | 是 | 产品列表在 JSON 中的点分路径，如 `response.resultData.productList`；数组下标用整数段 `items.0.list` |
| `model_field` | 是 | 列表项里的型号字段名 |
| `url_field` | 否 | 详情页 URL 字段名 |
| `model_url_template` | 否 | 无 `url_field` 时按型号拼 URL，`{model}` 占位 |
| `series_field` | 否 | 系列名字段；缺省按 `series_rule` 从型号推 |
| `family_id_field` | 否 | ★系列身份键，**优先于 series_field 作归并键**，防同名系列覆盖 |

配套：`identity.series_key` 建议设 `family_id`。

### 回退（可选但推荐）

`fallback_dom: true` + `link_selector` + `model_url_regex`：接口不可达/非 JSON/
list_path 落空时，自动回退走 `dom_anchor` 单层，不让整站失败。终止原因标
`xhr_fallback_*`。

---

## 找全率口径 ★

xhr_api 与入口页 HTML 无关，`probe.py --verify` 只校验**配置完整性**，
不给找全率。必须跑真实抓取，看审计：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/<code>.spec.json
```

审计的 `details.meta.item_count`（接口返回条数）与 `discovered_model_count`
（去重后型号数）对比官网标称，才是真实找全率。

---

## 常见失败与修法

| 症状 | 修法 |
|---|---|
| `xhr_api 缺 discover.api.xxx` | 补齐 url_template/list_path/model_field |
| `list_path 未定位到数组` | 路径写错；用浏览器看真实 JSON 结构逐段核对 |
| `接口响应非 JSON` | url_template 错，或接口需鉴权头/Cookie（当前引擎不带）→ 记缺口或回退 DOM |
| `api.url_template 越出允许域名` | 接口在别的子域，把该域加进 base_url 同源或确认非站外 |
| 同名系列被合并 | 配 `family_id_field` + `identity.series_key=family_id` |
| 型号数 = 0 但 item_count > 0 | `model_field` 字段名不对 |

---

## 已知边界（记缺口，别硬凑）

| 情况 | 处理 |
|---|---|
| 接口带**动态签名/token** | 当前引擎不带鉴权头，签名接口难复现 → 记 `gap: "xhr_api 需接口鉴权"`，走手写 |
| 接口需登录 Cookie | 同上 |
| 接口限频严格 | 走 RateLimiter，别绕过；仍不行记缺口 |

参考实现：`overseas/sites/samsung_us/adapter.py`（走 `__NEXT_DATA__` 内嵌 JSON，
是 xhr_api 的近亲——数据在初始 HTML 的 JSON 里，可用 `embedded_json` 档解析规格）。
