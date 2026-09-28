# 套路：多级发现（总览 → 系列 → 型号）

> 状态：✅ **引擎已实现**（`discover.mode="dom_anchor_two_level"`，2026-09-16）
> 目标站点：松下、夏普
> 离线等价性已验证（构造站 3 系列 6 型号、去重与干扰过滤正确）；
> 真实站需 `spec-crawl` 联网确认。

---

## 识别信号

| 信号 | 说明 |
|---|---|
| 入口页链接指向的是**系列页**而非型号页 | 点进去还有一层型号列表 |
| `product_url_pattern` 末段不含型号特征 | 如 `/viera/<系列名>.html` 而非型号 |
| 发现数 ≈ 系列数，远小于期望型号数 | 期望 40 型号只发现 8 个"型号" |
| 型号页 URL 形如 `/系列/型号/spec` | 中间还有一层 |

**快速验证**：从 `sample_product_hrefs` 挑一个链接，`probe.py --model` 打开它。
若它是**系列列表页**（含多个型号链接）而非规格页 → 确认是多级站。

---

## schema 模板

```json
{
  "spec_version": "1.0",
  "code": "panasonic_jp",
  "brand_name": "松下 VIERA（日本）",
  "region": "jp",
  "base_url": "https://panasonic.jp",
  "entry_url": "https://panasonic.jp/viera/products.html",
  "fetch": {
    "requires_browser": true,
    "interval_sec": 4.0,
    "entry_wait": "a[href*='/viera/']",
    "entry_extra_wait_ms": 5000,
    "page_wait": "table"
  },
  "discover": {
    "mode": "dom_anchor_two_level",
    "series_link_selector": "a[href*='/viera/']",
    "series_url_regex": "/viera/([A-Z]{1,3}\\d{2,4}[A-Z]?)\\.html$",
    "model_link_selector": "a[href*='/viera/products/']",
    "model_url_regex": "/viera/products/(?:TV|TH)-([0-9A-Za-z]+)/spec\\.html",
    "series_page_wait": "a[href*='/viera/products/']",
    "non_tv_filter": true
  },
  "spec": { "extract_order": ["generic_spec_js"] },
  "identity": { "series_key": "series_name", "model_normalize": "upper_alnum" },
  "expected": { "series_count": null, "model_count": null }
}
```

---

## 字段要点

| 字段 | 作用 | 坑 |
|---|---|---|
| `series_link_selector` | 入口页 → 系列页 | 收窄到系列专属路径，别用裸 `a[href]` |
| `series_url_regex` | 提系列名，**捕获组 1 = 系列名** | 要求"字母+数字"排除 SUPPORT/OPTION 等纯字母导航 |
| `model_link_selector` | 系列页 → 型号页 | |
| `model_url_regex` | 提型号，**捕获组 1 = 型号** | 从**型号页** URL 提，不是入口页 |
| `series_page_wait` | 打开系列页的等待锚点 | 缺省用 `model_link_selector`；懒加载系列页需设长一点 |
| `series_from` | 填 `"model"` 时按型号号归并系列 | 一般不填，用 `series_url_regex` 捕获的系列名即可 |

发现流程：
```
入口页 ──(series_link_selector + series_url_regex)──► 系列页URL集合
系列页 ──(model_link_selector  + model_url_regex )──► 型号页URL集合
        → [(系列名, [型号URL...])] → 引擎逐型号抓 SPEC（复用 parse_spec_model）
```

---

## 找全率口径 ★

两级站的找全率**无法从入口页单张 HTML 判定**（入口页只有系列，没有型号）。
`probe.py --verify` 对两级 spec 只评估"系列层能否发现"，型号找全率会标注
`需 spec-crawl 后看审计 discovered_model_count 判定`。

**必须跑真实抓取确认**：

```powershell
$env:OVERSEAS_DB = "data/sandbox_<code>.db"
py -3.12 -m overseas.cli spec-crawl --schema data/schema_drafts/<code>.spec.json
```

看审计输出的 `系列=N 型号=M`，对比官网标称数才是真实找全率。

---

## 常见失败与修法

| 症状 | 修法 |
|---|---|
| 发现 0 个系列 | `series_link_selector` / `series_url_regex` 错；或入口页系列由 JS 注入，加大 `entry_extra_wait_ms` |
| 系列对但型号 0 | `model_link_selector` / `model_url_regex` 错；系列页懒加载，设 `series_page_wait` |
| 混入非电视系列 | `series_url_regex` 收紧（要求字母+数字）；开 `non_tv_filter` |
| 同系列不同尺寸被拆成多个系列 | 正常按 `series_url_regex` 捕获归并；若仍乱，设 `series_from: "model"` 用型号号归并 |

---

## 参考实现

手写对照：`overseas/sites/panasonic_jp/adapter.py` 的 `series_entries()`。
配置驱动版逻辑与它等价，只是把两层选择器/正则写进 schema。
