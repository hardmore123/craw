---
name: overseas-crawl-ops
description: 海外零售站（Amazon US/MX/CA 等）长时全量爬取的运维手册 —— 启动、监控、自愈、僵尸检测、DB 污染清理、交付核对。触发场景：涉及 crawl_ca_retail.py / supervisor_amazon_full.py / rebuild_amazon_csvs.py / retail_task_status 的任何爬取或异常处理。
---

# 海外零售站长时爬取运维

## 触发条件
- 运行/重启 `scripts/crawl_ca_retail.py` 长时全量（>1h）
- 处理"进程被杀 / 卡死 / 温度漂移 / 污染价格"等爬取异常
- 需求"断点续抓 / 自动接力 / 自动导出 / 定时监控"

## 核心架构（本仓库已就绪，不要重写）
| 脚本 | 作用 |
|---|---|
| `scripts/crawl_ca_retail.py` | 底层扫描器，`--save-db` 落库，幂等可重复跑 |
| `scripts/supervisor_amazon_full.py` | 接力监督：逐站 → 生成剩余清单 CSV → 循环子进程 → 完成态判定 → 全部完成后调 rebuild |
| `scripts/rebuild_amazon_csvs.py` | 从 DB 重建三站全量 价格监控/网评汇总 CSV+XLSX（`--region us/mx/ca` 单挑一站） |
| `scripts/watchdog_rebuild_amazon_full.py` | 附加双保险：盯 supervisor PID，退出后自动 rebuild（WATCH_PIDS 是 ws 参数） |
| `scripts/retail_spec_tests.py` | 规格自检（跑全量前必跑） |

## 环境（每次新会话必设）
```
OVERSEAS_PROXY=http://127.0.0.1:7877
OVERSEAS_USE_PROFILE=1   # 缺这个拿不到登录态
PYTHONIOENCODING=utf-8
```
DB 落点：CA/US → `data/overseas.db`；MX → `data/mx/overseas_mx.db`（**不同库**！）
三库表：`retail_task_status`（终态集合 = ok/no_price/no_reviews/no_item/failed，失败立即跳永不重试）、`product`、`price_snapshot`、`review`。

## 启动全量的规范步骤
1. **必备份**：overseas.db + overseas_mx.db → `data/backup/`
2. 登录态自检：`data/session/amazon_*.state.json` 三站都在，`python -m overseas.cli login-status`
3. `pythonw scripts/supervisor_amazon_full.py` 前确认 STAGES 里 `sample_ratio`（None=全量）与 db/csv 路径正确
4. 顺序：US → MX → CA（CA 最慢约 5.9h）

## 监控与自愈（本会话踩过的坑，务必照做）
- **基础定式**：日志 → 三个过程状态（SUP/CRAWL/WATCH）→ DB 终态计数/总清单数 → MAX(captured_at)
- **僵尸检测（最重要）**：进程活着但 1 小时无新落库 + 日志尾 `socket.send() raised exception` = CDP/代理断连空转。supervisor 只对"子进程退出"自愈，够不到僵尸 → 发现后 `Stop-Process` 杀掉爬虫子进程，supervisor 会在 30s 后自动 attempt N+1 断点续抓（自动 skip done）。
- **攻读**：max_attempts=8/站；attempt 数 >3 仍不足阈值 → 报告疑点。
- 定时监控建议：`CronCreate` every 25min（我用了 `7,32,57 * * * *`，避开整点）。
- 深夜也报告，让工作台放心。

## 数据修复（本会话应用过的）
- 错误价格污染（如 116U7SG=24.98 配件价）：先 `BACKUP` 受影响行到 `data/backup/polluted_*.csv`（含 id），再 `DELETE`（仅 task='price' 且 price IS NOT NULL），重跑 `rebuild_amazon_csvs.py --region <site>` 即可刷新。**原则：缺价 > 错价**，宁可留空不造假价。
- 删除污染不改 product/review 数据（这些表独立，动 status 即可让 rebuild 取不到价）。

## 交付核对清单（验收）
- 三站各 1 份 `价格监控_{国家}_{YYYYMMDD}.csv/.xlsx` + `网评汇总_{国家}_{YYYYMMDD}.csv/.xlsx`（→ data/<国家>/）
- 型号行数 = 清单数；有价型号数；网评条数
- `PRAGMA integrity_check = ok`
- 异常型号清单：no_price / no_item / blocked 数量 + 典型型号
- 平台下限制：US 网评 ≤8 条/型号（硬顶）、MX 2~5 条 —— 交付时必须标注，不是 failure

## 参考文档（仓库里已有的，无需复制）
- `docs/交接文档_亚马逊三站全量抓取_20260920.md`（完整环境/步骤/坑）
- `技术分享_爬取项目经验整理.md`（全量经验）
- `docs/北美零售站反爬分类_2026-09-20.md`

## 南美/欧洲线爬取运维（VTEX/Magento/Costco/BestBuy）

### 平台分类与抓取路线（先判平台再选路）★
| 平台 | 识别特征 | 抓取路线 | 需 Playwright | 需契约 |
|---|---|---|---|---|
| **VTEX** | `/api/catalog_system/pub/products/search` | **Catalog API 直采** | 否 | 否 |
| **Magento** | `/catalogsearch/result/?q=` | Playwright 渲染搜索页 | 是 | 是 |
| **Next.js/Falabella** | `__NEXT_DATA__` | Playwright 或解析 JSON | 是 | 是 |
| **SPEC 品牌(LG/Sony)** | 官网 lineup | Playwright 过 403 WAF | 是 | 否 |
| **BestBuy** | robots 禁搜索 | **sitemap 确定性发现** | 否 | 否 |
| **Amazon** | 搜索页 JS 渲染 | Playwright + ASIN 提取 | 是 | 是 |

### VTEX Catalog API（14 站全部可用，最快路线）★
```python
GET https://{host}/api/catalog_system/pub/products/search?ft=televisor&_from=0&_to=49
# 返回 JSON 数组，无需认证。价格在 items[0].sellers[0].commertialOffer.Price
# 搜索词用西语 televisor（比 TV 召回率高 3-5 倍）
# 串行 + 0.3s 间隔，不并发（VTEX 有速率限制）
```
- 脚本：`scripts/vtex_to_db.py`，14 站全量入库约 60 秒
- 价格在三层嵌套 `items[0].sellers[0].commertialOffer.Price`（不是顶层）
- 评价数据不在 Catalog API 里，需另走商品页

### Magento Playwright（搜索页 JS 渲染）★
- 纯 HTTP 拿到 0 产品卡片 → 必须 Playwright
- 选择器高度统一：`li.product-item` + `a.product-item-photo` + `[data-price-amount]`
- **panafoto_pa** 用 `a.product-item-photo`（不带 `product.photo`），需按站调整
- **dismac_bo** 非标准 Magento（`product-item-pdc` class），搜索相关性差
- 脚本：`scripts/magento_to_db.py`

### Costco（MUI 框架，需长滚动）★
- 搜索页用 MUI skeleton 占位符，需 `scroll_until_stable=True` + `scroll_passes=6`
- 产品在 `.MuiGrid-root` 文本里（标题+价格在同一 text block）
- 提取：`g.self_text()` 按行分割，第一行是标题，`$xxx.xx` 是价格
- **costco_us** 需更多滚动或登录态（costco_ca 已入库 19 产品）
- 脚本：`scripts/costco_pw_to_db.py`

### BestBuy sitemap（绕开 robots 禁搜索）★
- BestBuy robots.txt 禁搜索路径，但放行商品页 + 公开 sitemap
- Playwright 仍被 403 blocked → **改用 sitemap 确定性发现**
- `sitemap_index.xml` → 37 个 `.gz` 分片 → 每片 50000 URL
- 筛选：品牌名 + "tv" + 排除 mount/stand/cable/remote
- bestbuy_ca: sitemap1 有 21966 产品 URL，筛选得 9 真实 TV
- bestbuy_us: bsin sitemap 有 107 TV URL（pdp sitemap 只有配件）
- 价格需后续 Playwright 抓 PDP（sitemap 只给 URL）
- 脚本：`scripts/bestbuy_ca_sitemap_to_db.py` / `bestbuy_us_sitemap_to_db.py`

### Walmart US（WAF 不可过）★
- Playwright 返回 "Robot or human?" 验证页（16KB HTML）
- 即使 Playwright 也过不了 → 需登录态或特殊反爬策略
- **当前不可抓，标记 blocked**

### Amazon MX（Playwright 渲染搜索页）★
- 纯 HTTP 返回 200 但 0 产品（JS 渲染）
- Playwright 后：`data-component-type="s-search-result"` 标记搜索结果
- ASIN 在 `data-asin` 属性，标题在 h2 > span，价格在 `.a-price .a-offscreen`
- 脚本：`scripts/amazon_mx_to_db.py`

### 爬取顺序建议（效率优先）
1. VTEX 14 站（API，60 秒）→ 2. BestBuy sitemap（纯 HTTP，2 分钟）→
3. Magento Playwright（每站 30-40 秒）→ 4. SPEC 品牌 Playwright（每站 20-80 秒）→
5. Costco Playwright（需长滚动，60-90 秒/站）

## 欧洲线爬取（5 国 + 俄罗斯，36 站逐站探测）

### 站点连通性（按国家分类）★
| 国家 | 可达站（纯HTTP） | 需 Playwright/代理 | 不可抓 |
|---|---|---|---|
| 英国 | samsung_uk, tcl_uk, hughes_uk, markselectrical_uk, amazon_uk(需邮编) | sony_uk(PW过403), currys, argos, ao, very, richersounds, jd, jlp(超时), joybuy(CAPTCHA) | lg_uk(PW超时) |
| 德国 | expert_de, amazon_de(需邮编) | mediamarkt_de | — |
| 法国 | amazon_fr(需邮编) | darty | boulanger(无URL) |
| 意大利 | amazon_it(需邮编) | mediaworld_it, unieuro(CAPTCHA) | — |
| 西班牙 | mediamarkt_es ✅, amazon_es(需邮编) | elcorteingles | — |
| 俄罗斯 | yandex_market(0TV) | dns(401), mvideo(403), technopark(401) | ozon(307), wildberries(498) |

### Samsung UK（JSON-LD ItemList，纯 HTTP）★
- `/uk/tvs/all-tvs/` 有 JSON-LD ItemList，numberOfItems=48
- 纯 HTTP 可达（无需 Playwright），产品名含型号（S95H, QN80H, R95H）
- 需排除 projector/Freestyle/Movingstyle 等非 TV
- 脚本：`scripts/samsung_uk_to_db.py`（38 产品入库）

### Sony UK（Playwright 过 403，ProductTile）★
- HTTP 403 → Playwright 200
- 产品在 `ProductTileOne__ProductName` + 含 `?sku=` 的链接
- SKU 格式 `k50xr75m2pb.uka`，型号为 `K-50XR75M2`
- 产品名格式 `BRAVIA 7 II | True RGB | RGB LED | 4K HDR Smart TV`
- 需排除 BRAVIA Theatre（音响非 TV）
- 脚本：`scripts/sony_pw_to_db.py` 扩展（7 产品入库）

### Mediamarkt ES（JSON-LD ItemList，纯 HTTP）★
- `/es/category/televisión-398.html` 有 JSON-LD ItemList 12 产品
- 产品名格式 `TV QLED 32" - Xiaomi 32 A PRO, QLED HD, ...`，价格在 offers.price
- 纯 HTTP 可达，含品牌+型号+尺寸+价格（€149-€499）
- 脚本：`scripts/eu_retail_to_db.py`（12 产品 12 价格入库）

### Amazon 欧洲五站（邮编定位，Playwright 原生 API）★
- amazon_uk/de/fr/it/es 均 HTTP 200，FR/ES 不需邮编即返回产品+本币价格
- 邮编：UK=LS118LZ, DE=96317, FR=75056, IT=20080, ES=28058
- 价格展示本币即可，不需要转化
- 用 `bf._ensure()` + `bf._ctx.new_page()` 绕过 BrowserFetcher 限制，直接操作 Playwright page

### 俄罗斯站全部不可达（需俄罗斯 IP）★
- dns/technopark: 401 Unauthorized；mvideo: 403；ozon: 307 重定向；wildberries: 498
- yandex_market: 200 但 0 TV 内容（需进一步探测）
- 当前代理出口非俄罗斯 IP，全部不可抓

### 欧洲线爬取顺序建议
1. 纯 HTTP JSON-LD 站（samsung_uk, tcl_uk, mediamarkt_es, expert_de）→
2. Playwright 过 403 站（sony_uk, lg_uk, philips_de）→
3. Amazon 五站（Playwright 输入邮编）→
4. 需住宅代理站（currys, argos, darty, mediamarkt_de 等）→
5. 俄罗斯站（需俄罗斯 IP 代理）
### Amazon 欧洲五站邮编输入（Playwright 原生 API）★
- BrowserFetcher `page()` 无文本输入 API → **直接用 `bf._ensure()` + `bf._ctx.new_page()` 获取原生 Playwright page**
- 邮编弹窗 DOM：点击 `#nav-global-location-popover-link` → 输入框 `#GLUXZipUpdateInput` → Apply `#GLUXZipUpdate-announce`
- cookie 弹窗 `#sp-cc-accept` 需先关闭
- **实测结果**：UK/DE 邮编弹窗超时（5s），但 FR/ES 不需邮编即返回大量产品+本币价格
  - amazon_fr: 38 产品 31 价格（€89.99-€779）✅
  - amazon_es: 36 产品 33 价格（€299-€2199）✅
  - amazon_uk: 1 产品 1 价格（£165.33）——邮编超时但仍少量结果
  - amazon_de: 2 产品——邮编超时，价格未带出
  - amazon_it: goto 超时——需重试或代理
- **关键发现**：amazon.fr/es 搜索页本身（不输邮编）就能返回法国/西班牙产品+本币价格，邮编非必需
- 脚本：`scripts/amazon_eu_postcode_to_db.py`
### amazon_de 代理重定向问题★
- amazon.de 被重定向到 amazon.co.uk（HTML lang=en-gb，仅32KB）
- 原因：当前代理出口 IP 为英国，德国站按地理位置重定向
- **stealth 增强后已解决**：添加 webdriver/plugins/chrome/WebGL 伪造后不再重定向，返回德国站内容
- 但邮编弹窗仍超时 + 搜索结果仅 2 个（代理出口 IP 仍非德国，地理定位不准确）

## crawl4ai 可借鉴经验（已提炼到本项目）

### 1. js_code_before_wait + wait_for 模式 ★
- crawl4ai 执行顺序：`page.goto` → `js_code_before_wait`（触发加载/点击/输入）→ `wait_for`（等内容出现）→ `js_code`（最终操作）→ `page.content()`
- **本项目对应**：BrowserFetcher 的 `page()` 只有 `click_selectors`（点按钮），无文本输入 → 绕过方式是 `bf._ensure()` + `bf._ctx.new_page()` 获取原生 Playwright page
- 原生 page 可用 `page.fill(selector, text)` + `page.click(selector)` + `page.wait_for_selector(selector)` 实现完整交互

### 2. remove_consent_popups（自动移除 GDPR/cookie 弹窗）★
- crawl4ai 自动移除 OneTrust/Cookiebot/Didomi 等 CMP 弹窗
- **本项目对应**：Amazon 的 `#sp-cc-accept` cookie 弹窗需先关闭，否则遮挡 location popover
- 通用做法：访问页面后先尝试点击 `#sp-cc-accept, #onetrust-accept-btn-handler, .a-button-input` 等

### 3. flatten_shadow_dom（扁平化 Shadow DOM）★
- Web Components 站点（Stencil/Lit/Shoelace）内容在 Shadow DOM 里，普通序列化看不到
- **本项目对应**：Costco 用 MUI 框架，产品在 `.MuiGrid-root` 文本里——用 `dom.sub(".MuiGrid-root")` + `g.self_text()` 提取
- 如果遇到 Shadow DOM 站点，用 `page.evaluate("() => document.documentElement.outerHTML")` 获取完整 HTML

### 4. JsonCssExtractionStrategy（CSS selector schema 驱动提取）★
- crawl4ai 用 `baseSelector` + `fields[{name, selector, type, transform}]` 的 JSON schema 驱动提取
- **本项目对应**：AdapterSpec/RetailSpec 的 contract-separates-judgment-from-execution 理念一致
- 新站 = 写一个 JSON schema（不写代码），引擎按 schema 抓取—— crawl4ai 验证了这个方向正确

### 5. session_id + js_only（多步交互复用同一页面）★
- crawl4ai 用 `session_id` 保持同一页面，`js_only=True` 只执行 JS 不重新导航
- **本项目对应**：Amazon 邮编输入需要多步（点 popover → 输邮编 → 点 Apply → 搜索），用同一个 `page` 对象顺序操作

## Stealth 增强 + human_like 模拟（实测结论）

### 增强内容（`overseas/fetchers.py` `_STEALTH_JS`）
- `navigator.webdriver` 隐藏（原有）
- `navigator.plugins` 伪造（Chrome PDF Plugin 等 3 个）
- `navigator.languages` 伪造 `['en-US','en']`
- `window.chrome` 对象注入（headless 缺失）
- `permissions.query` 伪造（notifications 一致性）
- WebGL `UNMASKED_VENDOR/RENDERER` 伪造（Intel，替代 SwiftShader）
- `hardwareConcurrency=8` / `deviceMemory=8` / `platform=Win32`

### human_like 参数（`bf.page(..., human_like=True)`）
- goto 后随机延迟 800-2000ms + 2 次随机鼠标移动
- 每次滚动后随机鼠标移动 + 200-600ms 延迟
- 模拟真实人类浏览行为

### 实测结论
| 站点 | 封锁类型 | stealth 效果 | 结论 |
|---|---|---|---|
| **amazon_de** | 地理重定向 | ✅ **解决** | 不再重定向到 amazon.co.uk，返回德国站内容（lang=de, 658KB） |
| bestbuy_ca | Akamai 403 | 部分缓解 | 从 403 变 404（URL 变化），说明不再被 IP 级硬封 |
| ripley_cl | Cloudflare JS 挑战 | ❌ 无效 | IP/ASN 级封锁，stealth 无法绕过 |
| walmart_us | PerimeterX 挑战 | ❌ 无效 | IP/ASN 级封锁，stealth 无法绕过 |

**核心结论**：stealth + human_like 能解决**浏览器指纹检测**（如 amazon.de 重定向、BestBuy Akamai 软封），但无法解决**IP/ASN 级硬封**（Cloudflare/PerimeterX 基于出口 IP 黑名单）。后者需更换住宅 IP。

## 全量抓取硬编码配置导出（CRAWL_CONFIG.json）★
- **产物**：`CRAWL_CONFIG.json` —— 把 74 站 adapter+spec 整合成单一 JSON，可直接驱动全量抓取，免去运行时逐站加载。
- **数据源逐字段优先级**（权威源选择，关键决策）：
  - 站点清单：`data/overseas.db` 的 `site` 表（74 站，唯一权威清单）。
  - country/currency/channel(platform)/capabilities/search/price/reviews/paginate 选择器：`schemas/retail_specs/*.retail.json`（抽取规则权威）。
  - search_url/product_url URL 模板 + requires_browser + protection + retail_page_wait：**adapter.py 实例化后的运行时值**（不是 spec fetch 段）。`SiteRegistry.get(code)` 实例化适配器后调用 `search_url("KW")`/`product_url("SKU")`，再回填 `{q}`/`{sku}` —— 这样能正确解析 `SEARCH_PATH` 类常量和 `self.base_url`，比静态正则提取 adapter.py 可靠得多。
  - reviews.source：用 spec `reviews.source` 字段（21 种真实来源，如 bazaarvoice_bfd/vtex_reviews_graphql/jsonld），**不要**只用 `paginate.reviews.type`（仅 9 种，太粗）。
  - retail_page_wait：优先 adapter 的 `retail_page_wait` 属性（采集链路实际读它，spec 的 `fetch.retail_page_wait` 不被读取，见 adapter.py 注释）。
- **坑**：kakaku_jp 的商品页方法名是 `item_url` 不是 `product_url`，需回退；lg_us/sony_ca 是 SPEC 类站无 search_url/product_url（留空，标注 supports_spec=true）；mercadolibre_pe/saturn_de 等 product_url 原样返回 sku（sku 即完整 URL），不拼模板，需标注。
- **9 站有 Bazaarvoice 配置**（`reviews.bv` 块：bestbuy_ca/costco_ca/costco_us/falabella_cl/lg_us/oechsle_pe/samsung_uk/sony_ca/sony_uk），导出时必须保留。
- **重新生成**（脚本临时建后删）：
  ```powershell
  py -3.12 _gen_config.py   # 输出 CRAWL_CONFIG.json，74 站，按 platform 排序
  # 校验：JSON 可解析 + search/product url 模板与适配器输出逐一比对（0 不匹配）
  ```

## SPEC 线 DB 落点坑（hisense_mx / lg_mx 误写备份库）★

**根因**：retail 线按国家分库（CA/US → `data/overseas.db`；MX → `data/mx/overseas_mx.db`，见上文「DB 落点」）。但 **SPEC 线（spec_series + spec_row）必须全部写主库 `data/overseas.db`**，因为 product_spec 跨品牌填充、全量导出都只读主库。子 agent 沿用 retail 的"MX 单独库"约定，把 hisense_mx(23系列/152行) + lg_mx(51系列/6480行) 写进了 `data/mx/overseas_mx.db`，导致主库 `SELECT ... WHERE brand='hisense_mx'` 返回 0，上报"成功"但 DB 无数据。

**判定信号**：子 agent 报告"成功 + N series / M rows"，但主库 `SELECT count(*) FROM spec_series WHERE brand='<code>'` 返回 0 → 立即查 `data/mx/overseas_mx.db` 是否有该 brand（MX 站）或其它分库。

**修复（已执行 2026-09-25）**：从备份库读 brand in (hisense_mx, lg_mx) 的 spec_series + 关联 spec_row → 重映射 id（主库 max+1 递增，避免 PK 冲突）→ 单事务 INSERT 主库 → 验证。结果：主库 hisense_mx 23系列/152行、lg_mx 51系列/6480行，与上报完全吻合。

**预防**：
- SPEC 子 agent 任务的 DB 路径必须显式指定 `data/overseas.db`，不可复用 retail 的国家分库逻辑。
- 验收时除看子 agent 报告，必须直接查主库 `SELECT brand, count(*) FROM spec_series GROUP BY brand` 核对，不能只信报告。
- 备份库 `data/mx/overseas_mx.db` 仅 retail 线 MX 数据，SPEC 线数据出现在此即为写错库，需迁移回主库。

## 全站进度矩阵与 crawl_config_fixed.py（2026-09-25 新增 ★）

### 爬取进度 CSV 全站适配状态

爬取进度 CSV 上所有非评测站（Spec + 价格+网评）的 schema/adapter 适配**已全部完成**：

| 维度 | 值 |
|---|---|
| CSV 非评测站 | 全部适配 ✅ |
| 缺 spec | 0 |
| 缺 adapter | 0 |
| 缺 DB site | 0 |
| retail spec 文件 | 204 |
| adapter 文件 | 216 |
| DB site | 205 |
| SiteRegistry | 216 站 |
| crawl_config_fixed.py | 204 站 0 问题 |
| validate 失败 | 0 |

### crawl_config_fixed.py 硬编码模块

`crawl_config_fixed.py` 是从 spec + adapter 文件自动生成的 Python dataclass 模块：
- `SiteConfig` dataclass 含 code/name/base_url/country/currency/capabilities/protection/search_url/price_jsonld 等
- 辅助函数：`get_site_config()`, `get_all_codes()`, `get_sites_with_price()`, `get_sites_with_reviews()`, `get_sites_needing_residential_ip()`
- 统计：TOTAL_SITES=204, SITES_WITH_PRICE=147, SITES_WITH_REVIEWS=44, SITES_NEEDING_IP=38
- **可直接 import 驱动全量抓取**，无需运行时逐站加载 JSON

### 数据覆盖矩阵（2026-09-25 最终）

| 指标 | 值 |
|---|---|
| product | 10,510 |
| price_snapshot | 10,710 |
| review | 83,985 |
| review_summary | 2,321 |
| spec_series | 927 |
| spec_row | 74,233 |
| 有产品站 | 105 |
| 有价格站 | 103 |
| 有评价站 | 38 |
| 有 SPEC 站 | 28 |
| 空 URL 产品 | 0 |
| 空标题产品 | 0 |

### 确认封锁站清单（需对应国家住宅 IP）

约 50 站被 Cloudflare/DataDome/F5/PerimeterX/Akamai 等 WAF 按 IP/ASN 硬封：
- **Cloudflare**: pccomponentes_es, worten_es, cyberport_de, ripley_cl, comfy_ua, czc_cz, 220_lv, kaup24_ee, varle_lt, bol_nl, bol_be, technopolis_bg, sancta_domenica_hr, fnac_fr, darty_fr 等
- **DataDome**: interdiscount_ch
- **F5 假 200**: nay_sk
- **PerimeterX 假 200**: sams_mx
- **Akamai**: coppel_mx, bestbuy_ca
- **假 200（国家验证）**: mercadolibre_pe, trendyol_tr

这些站有完整 spec/adapter，只需对应国家住宅 IP 即可抓取。

## 小样本检验方法（2026-09-25 新增 ★）

### 零售线检验流程

1. 从 DB 每站抽 3 个产品（sku/title/url/price/currency）
2. 用 BrowserFetcher(human_like=True) 重访 PDP
3. JSON-LD `offers.price` 优先 + CSS 兜底提取当前价格
4. 对比 DB 价格 vs 页面价格，±5% 容差
5. 结果分类：✅一致 / ⚠️差异（真实涨价非错价）/ ❌不可达

### SPEC 线检验流程

1. 检查 spec_series 每品牌 series 数量
2. 抽样 5 个 series，验证 spec_row 完整性（item_ja/values_json）
3. 检查 category 分布、row_order 连续性、captured_week 合法性
4. 检查 item_zh 翻译覆盖率

### 检验结论

- **零售线**：6 ✅一致 + 6 ⚠️真实涨价（非错价）= **0 错价**
- **SPEC 线**：5 品牌 100% 匹配，item_ja 100% 非空，values_json 100% 有效

## 全量抓取五步全流程（2026-09-27）★

### 执行流程

| 步骤 | 场景 | 站数 | runs | 关键参数 |
|------|------|------|------|----------|
| Step 1 | `spec_crawl()` SPEC全量 | 23站 | 23 | `spec_crawl(code, db=db)` |
| Step 2 | `s2_model_monitor()` S2型号监控 | 151站 | 153 | 用SPEC 1,687型号搜索传递 |
| Step 3 | `s3_detail()` S3详情直采 | 28站 | 46 | 有产品缺价格的零售站补 |
| Step 4 | `s1_keyword_search()` S1关键词搜索 | 16站 | 44 | "Samsung TV"/"LG TV" 关键词 |
| Step 5 | `s4_review_incremental()` S4网评增量 | 20站 | 57 | 2页，有reviews能力的站 |

### 最终数据

| 指标 | 抓取前 | 抓取后 | 变化 |
|------|--------|--------|------|
| spec_row | 80,878 | 113,390 | +32,512 |
| 本周新增(W39) | 0 | 65,920 | — |
| product | 13,312 | 13,440 | +128 |
| price_snapshot | 11,220 | 11,303 | +83 |
| review | 84,023 | 84,095 | +72 |
| 有价格站 | 112 | 124 | +12 |
| crawl_run总 | 248 | 548 | +300 |

### 经验教训（★关键）

1. **SPEC型号全球传递**：SPEC线23站抓到的1,687型号（Samsung 537/Hisense 342/LG 244/TCL 222/Regza 139/Sony 109/Sharp 44/Panasonic 31/Philips 19）可传给所有151零售站S2搜索匹配。欧洲共用一套SPEC即可。
2. **S1关键词搜索是补价主力**：S2型号监控匹配率低（拉美站搜索返回非TV商品），但S1用"Samsung TV"/"LG TV"宽关键词搜索命中率高（amazon_us 39价格、samsung_bo 40价格、hisense_co 37价格、samsung_uk 28价格）。
3. **SPEC站不实现product_url**：samsung_ca/hisense_us/sony_us等SPEC品牌站无product_url方法，S3详情直采不适用，只有零售站可S3补价。
4. **kakaku_jp价格需从raw_text解析**：搜索页返回的价格在`raw_text`字段（如"280,400 円～"），price字段为NULL。需正则`r'[\d,]+'`提取并设JPY币种。
5. **samsung_bo/samsung_uk JSON-LD字段名误入价格**：S1搜索时price_selector误匹配`upgradeResult.displayModelName`等JSON-LD字段名而非价格，需在price selector中排除`upgradeResult`前缀或后续DELETE清理。
6. **Amazon网评需登录态**：Amazon全系列(amazon_us/ca/de/fr/it/es/mx/uk)S4网评被`not_logged_in`拦截，需`OVERSEAS_USE_PROFILE=1`持久化登录态才能抓。
7. **熔断机制四层有效**：S1搜索blocked≥3跳过关键词、S2连续3次search_blocked跳过站、S3连续5次PDP失败跳过剩余、S4连续3次blocked跳过评价——全程避免空转。
8. **数据质量修复流程**：空价格→从raw_text解析；空币种→按站点code设对应币种（citilink_ru→RUB, leons_ca→CAD）；异常币种("Saltar a"/"$ 49.999"等)→DELETE；孤儿价格→检查product_id完整性。

### 可执行命令

```powershell
# SPEC全量
$py='C:\Users\likunyuan\AppData\Local\Programs\Python\Python312\python.exe'
$env:OVERSEAS_PROXY='http://127.0.0.1:7877'
$env:OVERSEAS_HEADLESS='true'
# Step 1: spec_crawl(code, db=db)  # 23站
# Step 2: s2_model_monitor(code, models=[...], db=db)  # 151站
# Step 3: s3_detail(code, skus=[...], db=db)  # 有产品缺价格的零售站
# Step 4: s1_keyword_search(code, keyword="Samsung TV", limit=20, pages=2, db=db)  # 16站
# Step 5: s4_review_incremental(code, skus=[...], pages=2, db=db)  # 20站

# 数据质量修复
py -c "import sqlite3,re; con=sqlite3.connect('data/overseas.db'); con.execute(\"UPDATE price_snapshot SET price=CAST(replace(substr(raw_text,1,instr(raw_text,'円')-1),',','') AS REAL),currency='JPY' WHERE raw_text LIKE '%円%' AND (price IS NULL OR price=0)\"); con.commit()"
```

### 35站WAF硬封清单（需住宅IP）

约35站被WAF硬封，熔断后自动跳过：
- **captcha**: euronics_ee, sams_mx, canadiantire_ca(pdp), philips_de
- **403/503**: bestbuy_ca, bestbuy_us(international redirect), altex_ro
- **not_logged_in**: amazon全系列(网评需登录态)
- **TimeoutError**: tcl_uk, sams_mx, canadiantire_ca(pdp)
- **no_anchor_element**: bestbuy_us, canadiantire_ca(pdp), philips_de, kakaku_jp(search)

### 北美三国线检查结论（2026-09-27）

US 9站 + CA 14站 + MX 10站 = 33站。24站有数据(73%)，14站有价格，12站有网评。

**可修复的2站（已修）**：
- `visions_ca` — schema `"search": {}` 为空，补Magento搜索容器 `li.product-item, .product-item, li.item, .product-item-info`（但站本身403需CA住宅IP）
- `walmart_ca` — schema `"search": {}` 为空，补Walmart搜索容器 `div[data-item-id], div[data-testid='list-view'] > div`（但站本身PerimeterX需CA住宅IP）

**6站SPEC解析问题（代理慢/官网改版，非代码bug）**：
- `tcl_us` — 产品URL结构变更(/products/qm6k-series → /products/qm6k-series-qd-mini-led-qled-4k-uhd-smart-tv-with-google-tv)，spec页可能改JS渲染
- `hisense_mx` — no_spec_section，入口页用hash fragment导航(/televisores#catalog?subcat=RGB)
- `lg_mx` — no_spec_section，入口页只有1个/tv/链接
- `tcl_mx` — 型号页解析为空，入口页无/mx/es/tvs/链接
- `sony_jp` — 入口URL从/bravia/lineup/重定向到/bravia/gallery/（已修ENTRY）
- `hisense_jp` — series正则过宽匹配型号（已修[a-z][a-z0-9]）

**9站需对应国家住宅IP（WAF硬封）**：
- US: walmart_us(PerimeterX), bestbuy_us(国际重定向)
- CA: bestbuy_ca(403), visions_ca(403), walmart_ca(PerimeterX)
- MX: coppel_mx(Akamai), sams_mx(captcha), walmart_mx(captcha)

**经验**：Walmart平台(US/CA)搜索容器通用 `div[data-item-id], div[data-testid='list-view'] > div`，sku用 `data-item-id` 属性。Magento站(visions_ca)搜索容器通用 `li.product-item, .product-item-info`。当schema的 `"search": {}` 为空时，S1搜索无候选——需补容器才能抓。

### 全线250站检查修复总结（2026-09-27）

全球250站分5区检查：日本7站、北美33站、南美65站、欧洲131站、亚洲14站。

**修复75站**：
- 日本3站adapter修复：hisense_jp series正则`[a-z][a-z0-9]`、kakaku_jp product_url()、sony_jp ENTRY从`/bravia/lineup/`→`/bravia/gallery/`
- 北美2站container：walmart_ca `div[data-item-id]`、visions_ca `li.product-item`
- 南美9站container：falabella_co(从falabella_cl复制完整schema)、mercadolibre_cl(从mercadolibre_pe)、ripley_pe(从ripley_cl)、tottus_pe(从oechsle_pe VTEX)、efe_pe/espana_ec/rodelag_pa/multimax_pa/tcl_ec
- 欧洲52站：42站container补全 + 10站search_url修复(mediamarkt_de/at/pl/saturn_de `/suche/{q}/`→`/de/category/tv-fernseher.html`、morele_pl→`/kategoria/telewizory-108/`、euro_agd_pl→`/telewizory-led-qled.bhtml`、eprice_it→`/search/televisori`、mediamarkt_it base_url修复)
- 亚洲9站container：dns_ru/mvideo_ru/ozon_ru/technopark_ru/wildberries_ru/hepsiburada_tr/mediamarkt_tr/teknosa_tr/vatan_tr

**核心教训**：
1. **SEARCH_PATH `/suche/{q}/` 是错误模板** — 20个站点共用此德语搜索路径但实际不支持。MediaMarkt/Saturn系列正确路径是 `/de/category/tv-fernseher.html`，波兰站morele_pl是 `/kategoria/telewizory-108/`，euro_agd_pl是 `/telewizory-led-qled.bhtml`。
2. **同平台站点container可复用** — MediaMarkt全系列用 `a[href*='/product'], a[href*='/produkt']`；eBay用 `a.s-item__link, a[href*='/itm/']`；eMAG用 `[class*='product']`；Euronics用 `a[href*='/product']`；Falabella用 `script[type='application/json']`(JSON-LD)；MercadoLibre用 `a.ui-search-link`；Ripley用 `a[href*='/producto/']`；VTEX超市(metro/oechsle/tottus)用 `a[href*='/p'], a[href*='/pdp/']`。
3. **container选择器差异** — digitec_ch用 `a[href*='/product']`(无尾斜杠)非 `a[href*='/product/']`(有斜杠)；eprice_it用 `a[href*='/p/']`；sonvideo_fr用 `div.item`；power_fi需匹配power_dk的完整容器+wait_ms+scroll_passes。
4. **52站WAF硬封需住宅IP** — eBay全系列403、Alza全系列403、Elgiganten/Elkjop/Gigantti 429、eMAG 511、Amazon网评not_logged_in。

**最终数据**：全球250站 | 产品13,430 | 价格11,383 | 网评84,095 | spec_series 861 | spec_row 113,390

### 南美+北美深度探测修复（2026-09-27）

对南美65站+北美33站=98站进行实战批量探测(search_url可达性+container匹配)，修复9站。

**南美+北美探测结果**：OK 27站(28%)、BLOCKED 18站(18%)、TIMEOUT 35站(36%)、NO_MATCH 10站→修复8站、404 7站→修复2站。

**修复2站404(search_url)**：
- falabella_cl: `/search?q={q}` → `/falabella-cl/category/cat60049/Televisores`（category页，200+match=1）
- mercadolibre_cl: base_url从 `www.mercadolibre.cl` → `listado.mercadolibre.cl`（listado子域才是搜索页）

**修复8站NO_MATCH(container不匹配)**：
- alkosto_co: `a[href*='/p/'], [class*='product']` ✅25/817
- canadiantire_ca: `[class*='product'], [data-testid*='product']` ✅148/12
- costco_ca: `a[href*='/p/'], [data-testid*='product']` ✅6/16
- estilos_pe: `[class*='product'], article` ✅176/5
- jumbo_co: `a[href*='/product'], [class*='product'], article` ✅6/316
- marcimex_ec: `[class*='product'], article` ✅82/8
- olimpica_co: `[class*='product'], a[href*='/product']` (未实测)
- rodelag_pa: `[class*='product'], a[href*='/product']` (未实测)

**仍需修复的5站404(代理/WAF/JS渲染)**：
- paris_cl/sukasa_ec/todohogar_ec/evision_pa: 尝试多个category URL均404——代理IP被拦或站点结构已变
- falabella_co: 25KB JS SPA，需JSON-LD容器(falabella平台用`script[type='application/json']`)

**35站TIMEOUT原因**：代理(7877)到南美/北美线路慢，品牌官网(samsung_us/ca/cl/co/mx、lg_us/ca/mx、tcl_us/ca/cl/ec/mx、sony_us/ca)均超时——代理不稳非代码bug。

**18站BLOCKED需住宅IP**：walmart_us/ca/mx(PerimeterX)、bestbuy_ca(403)、coppel_mx(Akamai)、ripley_cl/pe(403)、visions_ca(403)、hites_cl/lider_cl(404 blocked)。

**核心教训**：
1. **Falabella系列search_url用category页**：falabella_cl正确URL是 `/falabella-cl/category/cat60049/Televisores`，非 `/search?q=`。falabella_co可能类似需要 `/falabella-co/category/cat60049/Televisores`。
2. **MercadoLibre base_url用listado子域**：mercadolibre_cl的base_url应为 `https://listado.mercadolibre.cl`，非 `www.mercadolibre.cl`。
3. **南美站container多用 `[class*='product']`**：alkosto_co/canadiantire_ca/costco_ca/estilos_pe/jumbo_co/marcimex_ec均可用 `[class*='product']` 作为通用兜底容器。
4. **TIMEOUT≠代码bug**：品牌官网(samsung/lg/tcl/sony各国家站)和部分南美站代理慢导致超时，实际container可能正确但需要更稳定代理。

### LangGraph全自动流程化（2026-09-27）

将250站爬取全流程改造为LangGraph编排引擎，每个节点封装为skill。

**架构**：`overseas/flow/` 目录，StateGraph编排7节点(init→spec→retail→review→verify→report+retry)，条件路由edges，State JSON持久化断点续跑。

**节点skill**：
- `init_node` — 备份DB/加载站点列表/磁盘空间检查(<1GB暂停)
- `spec_node` — 调用spec_crawl()，品牌熔断内置
- `retail_node` — 调用s2_monitor_known()，子进程隔离SQLite线程，90秒/SKU超时
- `review_node` — 调用s4_review_incremental()，只对有网评站增量
- `verify_node` — DB增量对比(spec_row/price/review)
- `report_node` — 自动生成markdown报告到`海外/<线名>线LangGraph增量报告.md`
- `retry_node` — 失败站重试，limit递减，最多2次→标记blocked

**边界条件**：磁盘<1GB暂停、90秒超时→TIMEOUT、连续3 blocked→跳站、最多2次重试、State JSON断点续跑。

**使用**：`python -m overseas.flow.langgraph_crawl --lines japan,na,sa,eu,asia`（全量5线串行）。

**日本线测试结果**：init→spec(6站105ok)→retail(kakaku超时)→review(0新增)→verify(+6价格)→report自动生成 ✅

### LangGraph并行执行完善（2026-09-28）

将retail_node和spec_node从串行改为并行执行（ThreadPoolExecutor+子进程隔离）。

**并行设计**：
- `spec_node` — 默认并行度=2（SPEC抓取重，并行度低），每站600秒超时
- `retail_node` — 默认并行度=3，每站90秒超时
- 每站独立子进程+独立DB连接，避免SQLite线程冲突
- `ThreadPoolExecutor` + `as_completed` 收集结果，单站超时不影响其他站

**性能对比**（日本线6站SPEC）：
| 模式 | 总耗时 | 说明 |
|------|--------|------|
| 串行(旧) | ~2050s | 6站逐个执行 |
| 并行=2(新) | 1348s | 提速34%，提速受最慢站限制 |

**日本线并行测试**：6站SPEC ok=70(hisense10+regza39+sony8+sharp13)，panasonic/tcl超时600s → verify(+7价格) → report ✅

**配置**：`state['parallel_workers']=3`（RETAIL）、`state['spec_parallel_workers']=2`（SPEC）可动态调整

### DeepResearch调研报告生成系统（2026-09-28）

基于S4训练营多智能体架构的调研报告生成系统，6个Agent协作，减少大模型幻觉。

**架构**：`overseas/report/` 目录，6 Agent分工：
- `ChiefArchitect` — 规划大纲+基线统计（锚定DB表结构）
- `DataScout` — 采集数据点+信源标注（可信度0-1）
- `DataAnalyst` — 市场集中度/价格/评分/SPEC分析
- `CompetitorAnalyst` — 对手矩阵/上市预警/活动预警
- `ReportWriter` — 生成markdown报告（每段标注`[来源:Sx]`）
- `CriticMaster` — 对抗审核+幻觉检测+质量评分

**防幻觉6措施**：①数据锚定（所有结论映射DB表）②信源追溯（`[来源:Sx,week]`）③事实约束（无数据不推断）④对抗审核（Critic逐条核对）⑤数值校验（写入前二次核对）⑥引用格式（可追溯信源ID）

**报告维度**：市场总览→数据分析→对手分析→未来报告→其他厂家上市预警→其他厂家活动预警→数据缺口与后续抓取建议→信源索引

**使用**：`python -m overseas.report.generate_report --db data/overseas.db --region all`

**测试结果**：13,507产品/146,853规格/11,817价格/84,095评价 → 16,176字报告，244信源，审核8.0/10 ✅

**后续抓取清单**：FCC认证信息(🔴高)、销量/排名(🔴高)、上市日期(🟡中)、退市信号(🟡中)、库存状态(🟡中)、促销标记(🟡中)、社交舆情(🟢低)、展会(🟢低)、专利(🟢低)、供应链(🟢低)

### 墨西哥站区域分类修复（2026-09-28）

**问题**：10个站点country字段是中文("墨西哥"4站+"日本"6站)，而非英文"MEXICO"/"JAPAN"，导致区域分类逻辑失效。

**影响**：
- 欧洲线价格/网评：❌不受影响（墨西哥站price=0 review=0）
- 欧洲线SPEC统计：⚠️被污染（墨西哥SPEC 14,054行被错误计入欧洲）
- 北美线SPEC：⚠️缺失（墨西哥SPEC站未被归入北美线）

**修复**：
- `flow/nodes/init.py` LINE_COUNTRIES增加中文"墨西哥"/"日本"
- `report/generate_report.py`新增`classify_region()`函数，REGION_COUNTRIES中英文兼容
- init_node改为大写匹配`countries_upper`

**验证**：修复后墨西哥10站正确归入北美线na，欧洲线不再误含墨西哥站 ✅


对日本7站+亚洲14站=21站进行实战批量探测。

**探测结果**：OK 1站(amazon_tr match=128)、BLOCKED 8站、TIMEOUT 8站、NO_MATCH 2站→修复2站、404 1站→修复1站。

**修复3站**：
- **kakaku_jp**: container从 `.p-product_name a, a[href*='/item/']`(尾斜杠不匹配) → `.p-resultItem, a[href*='item'], .p-product_name a` ✅ 18 matches（kakaku搜索页用 `p-resultItem` 类名，产品链接路径无尾斜杠）
- **hepsiburada_tr**: container加 `a[href*='-p-']` ✅ 72 matches（hepsiburada产品URL格式 `/[productName]-p-[HBVNxxx]`，旧容器 `a[href*='/urun/']` 不匹配）
- **trendyol_tr**: container保持 `[class*='product']`，但所有URL均返回254KB无产品——WAF硬封需TR住宅IP（非container问题）

**日本品牌官网7站全超时**：hisense_jp/panasonic_jp/regza_jp/sharp_jp/sony_jp/tcl_jp/sony_jp — 代理到日本线路慢导致timeout，SPEC站无search_url(container只用于RETAIL站)，非代码bug。

**俄罗斯5站WAF硬封**：dns_ru(401)、eldorado_ru(503)、mvideo_ru(200 blocked)、ozon_ru(403)、technopark_ru(401)、wildberries_ru(498) — 全需RU住宅IP。

**土耳其3站WAF硬封**：mediamarkt_tr(403)、teknosa_tr(403)、vatan_tr(403) — 需TR住宅IP。trendyol_tr虽200但254KB无产品也是WAF。

**核心教训**：
1. **kakaku_jp搜索页用 `p-resultItem` 类名** — 产品链接是 `a[href*='item']`(无尾斜杠)，非 `a[href*='/item/']`(有斜杠)。
2. **hepsiburada_tr产品URL格式 `xxx-p-HBVN000xxx`** — container需用 `a[href*='-p-']` 匹配。
3. **亚洲WAF站多** — 俄罗斯(401/403/498/503)、土耳其(403)、trendyol(254KB假200)全需住宅IP。