# 套路：VTEX Catalog API 直采

> 适用站点：VTEX 平台零售站（南美主力：oechsle_pe / plazavea_pe / carsa_pe /
> exito_co / multicenter_bo / japon_ec / olimpica_co 等 15 站）。
> 已验证：2026-09-24，15/15 站全部可用。

## 什么时候用

站点基于 VTEX 平台时，**不要写 RetailSpec 选择器、不要用 Playwright 渲染搜索页**——
VTEX 暴露了公开 Catalog API，可直接拿 JSON 产品列表，速度比浏览器快 10 倍且不触发反爬。

识别 VTEX 站的特征：
- 首页 HTML 含 `vtex` / `__bridge` / `/busca?query=`
- 搜索路径是 `/busca?query=<kw>`
- `robots.txt` 或页面源码含 `catalog_system`

## API 端点

```
GET https://{host}/api/catalog_system/pub/products/search?ft={keyword}&_from=0&_to=49
```

- `ft` = 全文搜索词（如 `televisor`）
- `_from` / `_to` = 分页偏移（0-based，含两端），每页最多 50 条
- **无需认证**，`Accept: application/json` 即可
- 返回 JSON 数组，每个元素是一个产品对象

## 产品字段映射

```python
p = data[0]  # 单个产品
p["productId"]      # 站内产品 ID
p["productName"]    # 商品标题（含型号、尺寸、品牌）
p["brand"]          # 品牌名
p["link"]           # 商品页 URL
p["items"][0]["name"]          # SKU 名称
p["items"][0]["itemId"]       # SKU ID
p["items"][0]["sellers"][0]["commertialOffer"]["Price"]       # 当前售价
p["items"][0]["sellers"][0]["commertialOffer"]["ListPrice"]   # 原价
p["items"][0]["sellers"][0]["commertialOffer"]["Available"]   # 是否有货
p["items"][0]["sellers"][0]["commertialOffer"]["PriceCurrency"] # 货币
p["items"][0]["images"][0]["imageUrl"]  # 主图
```

## 完整抓取流程

```python
import urllib.request, json

def crawl_vtex(host, keyword="televisor", max_pages=20):
    all_products = []
    for page in range(max_pages):
        frm = page * 50
        url = f"https://{host}/api/catalog_system/pub/products/search?ft={keyword}&_from={frm}&_to={frm+49}"
        resp = urllib.request.urlopen(url, timeout=12)
        batch = json.loads(resp.read().decode("utf-8"))
        if not batch:
            break  # 空页 = 到底
        all_products.extend(batch)
        if len(batch) < 50:
            break  # 不足一页 = 最后一页
    return all_products
```

## 型号提取

VTEX 的 `productName` 通常含型号，但格式不统一。从标题正则提取：

```python
import re
def model_from_title(name):
    # 常见 TV 型号模式：43U6100 / 55U7SV / 65QD8SF / 50S5K
    m = re.search(r'(\d{2}[A-Z]{1,3}\d?[A-Z]{0,3}\d?)', name.upper())
    return m.group(1) if m else ""
```

## 已验证站点清单（2026-09-24 更新）

| code | host | 货币 | 响应 | 样例品牌 | 样例价 |
|---|---|---|---:|---|---:|
| oechsle_pe | www.oechsle.pe | PEN | 2.4s | NINTENDO | 2599.0 |
| plazavea_pe | www.plazavea.com.pe | PEN | 2.1s | TCL | 1899.0 |
| estilos_pe | www.estilos.com.pe | PEN | 9.2s | HISENSE | 999.0 |
| metro_pe | www.metro.pe | PEN | 2.8s | TCL | 429.0 |
| carsa_pe | www.carsa.pe | PEN | 4.0s | LG | 299.0 |
| credivargas_pe | www.credivargas.pe | PEN | 2.0s | PHILIPS | 882.0 |
| japon_ec | www.almacenesjapon.com | USD | 3.0s | Diggio | 143.48 |
| orvehogar_ec | www.orvehogar.com | USD | 2.9s | Diggio | 143.48 |
| marcimex_ec | www.marcimex.com | USD | 5.3s | LG | 646.86 |
| comandato_ec | www.comandato.com | USD | 2.5s | Indurama | 235.64 |
| exito_co | www.exito.com | COP | 4.6s | HYUNDAI | 980900.0 |
| jumbo_co | www.jumbocolombia.com | COP | 3.7s | CHALLENGER | 3299900.0 |
| multicenter_bo | www.multicenter.com | BOB | 2.1s | Hisense | 4789.0 |
| elektra_gt | www.elektra.com.gt | GTQ | 6.1s | SAMSUNG | 1345.0 |
| olimpica_co | www.olimpica.com | COP | 8.7s | TCL | 8287900.0 |

## 注意事项

1. **不要并发请求同一站点**——VTEX 有速率限制，串行 + 0.3s 间隔安全。
2. **`ft` 搜索词用西班牙语**：`televisor`（电视）比 `TV` 召回率更高。
3. **价格在 `items[0].sellers[0].commertialOffer.Price`**，不是顶层字段；
   `ListPrice` 是原价，`Price` 是当前售价。
4. **`productName` 可能不含完整型号**——部分站标题是营销文案（如 "Televisor TCL QLED 32 Google Tv"），
   需结合 `link` URL slug 交叉提取型号。
5. **评价数据不在 Catalog API 里**——需另外调用 `/api/catalog_system/pub/product/{id}/specification`，
   或商品页的 Bazaarvoice/PowerReviews 接口（见 `thirdparty_review_api.md`）。
6. **`_to` 有硬上限（exito_co 实测 ≤ 2549）**——`resources` header 声明总数 3280，
   但 `_to > 2549`（page 51+）返回 **400 Bad Request**（非 403 限流，是平台硬限制）。
   单次查询最多取 2550 条，无法靠加页取全。判断方法：`_to=2549` 成功、`_to=2598` 起 400。
   若总数 > 2550，需用排序/筛选分片（见下条）或换分类路径 `search/{cat}` 再翻一次。
7. **默认排序翻页会因代理限流被跳过中段页**——`continue` 跳过 SSL EOF/403 失败页会漏产品
   （exito_co 实测默认排序 page 13-29 漏 ~800 条，DB 停在 2482 而非 3274）。
   补救：用 `&O=OrderByPriceAsc` 重翻 0-50 页，**慢速 2s/页 + 指数退避重试(1/2/4s, 3次)**，
   因不同排序下中段页产品与默认排序不重叠可补全。三个排序(PriceAsc/PriceDesc/TopSaleDesc)
   尾部新增相同，**用 PriceAsc 一个即可避免重复**。最终 3274/3280，差 6 属去重边界。

## 与 RetailSpec 的关系

VTEX 站**不需要写 RetailSpec 契约**来抓价格目录——API 直接给 JSON。
但如果需要抓**商品页评价**（评价选择器），仍需为每个站写一份 RetailSpec
（因为评价渲染方式各站不同）。推荐两步走：

1. **第一步**：用 API 拉全量价格目录（本套路），产出 CSV + 入库 `price_snapshot`
2. **第二步**：对需要评价的站，单独写 RetailSpec 契约跑 `crawl_sa_retail.py`

## 参考脚本

- `scripts/vtex_full_catalog.py` — 已有的全量目录抓取脚本，已定义全部 14 站
- `scripts/vtex_catalog_stock.py` — 库存 + SPEC 短码交叉匹配
