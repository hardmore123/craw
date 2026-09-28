# 判断经验库

> 每条都来自真实站点的实测教训，不是推测。新增经验请注明来源站点。
> 初始内容来自 `docs/LLM自动适配器技术方案.md` §9.13 / §11.4 / §12.4 的沉淀。

---

## 一、选产品链接选择器

### 1.1 看纯度，不要只看命中数 ★核心

品牌站的导航栏、页脚常用宽泛结构（`li a[href]`），命中数天然比真实产品选择器高，
但绝大多数是分类页/其它品类/导航链接。

实测对比（来源：Hisense US / 秘鲁 Hisense）：

| 站点 | 泛选择器 | 产品选择器 | 结果 |
|---|---|---|---|
| Hisense US | `li a[href]` 命中 66~94，纯度 0.42 | `a[href*='/product-page/']` 命中 29，纯度 **0.97** | 产品选择器胜出 |
| 秘鲁 Hisense | `li a[href]` 纯度 0.56 | `a[href*='/tv/']` 纯度 **0.69** | 产品选择器胜出 |

`probe.py --entry` 的 `link_selector_ranking` 已按纯度排序，直接看 `model_like_ratio`。

### 1.2 宽泛路径在"全站产品共用路径"的站会混入非电视

| 站点 | 坑 | 解法 |
|---|---|---|
| Sony JP | `/products/` 是全站共用，会混进耳机 WH-1000XM6、相机 ILCE-7RM6、手机 | 收窄到 `a[href*='/bravia/products/']` |
| REGZA JP | 站内既有 `/tv/lineup/` 也有 `/bd-dvd/lineup/`（蓝光录像机） | 收窄到 `a[href*='/tv/lineup/']` |

**规律**：优先用"电视/品牌专属的精确路径"，靠纯度排序自然胜出。

### 1.3 型号 URL 的判据

`_looks_like_model_url` 要求末段含"字母紧邻 ≥2 位数字"（如 `75u7sg`、`65OLED759`）。
这能排掉 `4k-uled`、`4k-uhd`、`smart-tv-platforms` 这类只有孤立单数字的导航词。

---

## 二、分页与动态加载

### 2.1 只有"编号页码"是可靠分页信号 ★核心坑

| 信号 | 可靠性 | 处理 |
|---|---|---|
| 编号页码（`aria-label='Show page N of results'`） | **可靠** | 判为分页站，配 `snippet_ref` |
| 孤立的"下一页 / next"按钮，无编号页码 | **不可靠** | 极可能是轮播/推荐区按钮，**忽略** |

翻车案例（Sony BRAVIA JP）：静态列全的站，推荐区轮播有 next 按钮 → 被误判成分页 →
LLM 加了 `numbered_pages_generic` → 实抓时点错按钮把页面导航走了 → **发现 0~1 个型号**。
去掉分页配置后立刻 8/8。

`spec_probe.build_probe_report` 已修正此判定，但**智能体仍要自己把关**：
`load_more_hint=none` 时无论 `pager.has_next_button` 真假，都不要写 `discover.paginate`。

### 2.2 替换式分页 vs 追加式分页

| 类型 | 表现 | 通用片段能否处理 |
|---|---|---|
| 追加式 | 点击后链接数增长 | 能 |
| **替换式** | 点击后换掉当前一页，链接数**不增长**（Philips CA / Next.js） | 能，但依赖片段内部重扫页码 |

替换式的识别不能靠"点击后链接是否增长"（不增长），只能靠**检测到编号页码控件**。
`_detect_pager` 会汇报 `paginate_replace`。

### 2.3 分页站的找全率口径

**不能用首屏 HTML 卡分**，要用引擎执行分页片段后的实际发现数。
Philips CA 首屏只有第 1 页 12 个型号，用首屏算就是 48%，实际翻完是 25/25 = 100%。

---

## 三、JS 片段库

### 3.1 片段选型优先级

1. `numbered_pages_generic` — 编号页码型首选，已鲁棒化（每翻页重扫页码 + visited 队列翻到尽头）
2. `philips_paginate` — 站点专用，仅在通用片段失效时用

### 3.2 三层转义坑 ★血泪教训

JS 片段嵌在 Python raw string 里，`new RegExp('...')` 的反斜杠要按
**Python 字面 → JS 字符串 → 正则** 三层数清。

历史 bug：`new RegExp('\\\\bpage\\\\s+')` 四个反斜杠在 JS 里求值成 `\\b`
（匹配**字面反斜杠**），页码按钮永远匹配不到，翻页全失败，表现为"语法对但运行时正则失效"。
正确写法是双反斜杠 `'\\b'`。

**改片段后必须**：`node --check` + 针对真实 `aria-label` 做匹配自测。

---

## 四、规格页解析

### 4.1 选档规律

| 页面特征 | `extract_order` |
|---|---|
| 有内嵌 JSON 承载完整规格（`"specification":{...csChapter...}`） | `["embedded_json", "generic_spec_js"]` |
| 规格在 DOM 表格 / dl / 键值对里 | `["generic_spec_js"]` |

`generic_spec_js` 是通用多策略抽取，**建议始终放在最后当兜底**。

### 4.1b model_url_regex 不要硬依赖 URL 尾斜杠 ★

引擎发现时对每个 href 先过 `clean_url()`，它会**去掉查询串、片段和末尾的一个斜杠**。
所以：

- 若型号在 URL **最末段**（如 `/tv/.../55U8N`），`clean_url` 后没有尾斜杠，
  正则**不能**写成 `...(\w+)/$` 强要求结尾 `/`，否则全部匹配失败（发现 0 个）。
- 若型号**后面还有段**（如 Philips `/c-p/55OLED760_12/overview/`），
  `clean_url` 去掉最后一个 `/` 后仍是 `.../55OLED760_12/overview`，
  正则 `/c-p/([A-Za-z0-9]+)_[A-Za-z0-9]+/` 里的 `_..../` 能正常匹配（型号非末段）。

判断方法：拿 `probe.py --entry` 的 `sample_product_hrefs`，先在脑子里对它做
"去查询串 + 去一个尾斜杠"，再套正则。发现 0 个但选择器命中不为 0 时，**首先怀疑
正则尾部的 `/`**。

### 4.2 内嵌 JSON 的 require_field 坑

`chapters_field` 同时被当作 `require_field`——只接受**含该字段**的根对象。
Philips 页面里有多个 `specification` 键，只有含 `csChapter` 的那个才是规格数据。
不设这个约束会命中同名但无规格的对象，抽出 0 行。

### 4.3 品牌官网价格常需 JS 渲染后才进 JSON-LD（Samsung UK 实测）★

Samsung UK `/uk/tvs/all-tvs/` 的 JSON-LD ItemList 48 条，但**纯 HTTP（urllib+代理）
抓到的 ItemList 不含 `offers.price`**——价格字段是客户端 JS 注入后才出现的。
同一 URL 用 Playwright `domcontentloaded + 滚动` 后再读 `page.content()`，
48/48 条都带 `offers.{price, priceCurrency, availability}`。

规律：品牌官网列表页若"产品有了但价格恒为空"，先怀疑**价格靠 JS 渲染注入**，
别误判成站点不给价。验证方法：同一 URL 分别用 HTTP 和 Playwright 抓，对比
JSON-LD `offers` 字段是否存在。补价用 Playwright 重抓即可，不必逐个 PDP。

匹配入库用**规范化 URL 精匹配**（去尾 `/` + 小写）最稳：套组 URL
（`<TV_CODE>uxxu-hw-...-f-<套组码>/`）与单品 URL 不同，但 DB 入库时存的就是
套组/单品各自的真实 URL，逐一对得上。退化方案：按 URL 末尾 `*xxu` 产品码 +
bundle/size 消歧。另注意 `availability=OutOfStock` 的商品**仍带 price**（≠0），
`in_stock` 要按 availability 分流，不能拿"有价"反推在库。

---

## 五、喂 LLM 的 HTML 预处理

### 5.1 整页压缩会帮倒忙（若用于生成 CSS 选择器）

实测（来源：§9.10 对比实验）：

| 喂法 | 结果 |
|---|---|
| 原始 HTML（截断 30K） | 94 分 PASS，2 轮收敛 |
| `prune_html` 旧正则版压缩后再喂 | **0 分 FAIL** — 压缩破坏 `id`/`class`/结构线索，LLM 只能生成 `h1`/`.price` 这类通用猜测 |

**正确做法**：压缩版当"地图"定位目标区域，**原始 DOM 片段当"施工面"**生成选择器。

### 5.2 prune_html 已升级为 lxml 去重版

重复卡片去重（同 `tag + class + 文本hash` 只留第一个）后：
112K 字符 → **418 字符**，且页尾关键元素 4/4 保留（旧正则版截断 30K 时 0/4）。
列表页 N 个同构产品卡，喂 LLM 留 1 个样例就够。

---

## 六、诊断纪律

### 6.1 每轮只动一个变量

换档 / 改选择器 / 加交互三类改动**不要同时做**，否则分不清哪个起作用。
§9.13 那次找全率 48%→100% 的排查，正是靠"隔离片段这个单一变量（写死
`numbered_pages_generic`）"才定位到真实根因。

### 6.2 初判往往是错的，要拿决定性证据

同一次排查中，初判是"maxPage 只首屏探测一次翻不到尽头"，但证据推翻了它：
首屏其实有 122 个链接、去重后 12 个型号，两种正则提取都是 12 → 排除正则问题 →
最终定位到 `pageBtn` 正则的转义 bug。

**要找的是"能排除其它可能"的证据，不是"符合猜想"的证据。**

### 6.3 PowerShell 管道会吞 stderr

中文异常/traceback 不显示，表现为"进程静默退出"。排查必须加 `2>&1`。
另：本仓库 shell 会话中文可能显示为乱码（代码页问题），不影响文件实际内容。

---

## 七、判定"该手写适配器"的信号

出现以下任一，**及时止损**，不要硬凑（来源：Hisense US 实测）：

- 型号 slug 毫无规律，通用正则无法可靠框住（`75u7sg` / `tv43qd40r` / `40qd40r` 混用）
- Load More 按钮通用点击候选点不开
- 需要多级页面（总览→系列→型号）才能拿到型号 —— 当前引擎无此能力
- 需要调用带动态签名的接口

记入 `cases.jsonl` 标 `verdict: "needs_handwritten"`，并写清**缺哪个引擎档位**——
这是 P0 排期的直接输入。

---

## 八、当前已知引擎缺口（撞上就记，别绕）

| 缺口 | 影响站点 | 状态 |
|---|---|---|
| ~~多级发现（总览→系列→型号）~~ | 松下、夏普 | ✅ **已实现** `dom_anchor_two_level`（2026-09-16），见 playbook/multilevel.md |
| ~~`xhr_api` 接口发现~~ | TCL、Samsung、LG | ✅ **已实现** `xhr_api`（2026-09-16），见 playbook/xhr_api.md |
| ~~`query_param` 分页（`?firstResult=N`）~~ | LG | ✅ **已实现** `paginate.type=query_param`（2026-09-16），见 playbook/numbered_paginate.md |
| 点击 tab 展开才出规格 | TCL 类 SPA | 无此解析档位（P1） |

> **P0 覆盖面缺口已全部清空**（多级 / xhr_api / query_param 均落地）。
> 剩余为 P1 生产化（库表/health/审核门）与 P2 合规，见 docs/任务表与验收标准.md。

---

## 九、SPEC 线小样本检验经验（2026-09-25 新增 ★）

### 9.1 检验方法

SPEC 线检验 6 项：
1. **series/rows 计数** — 与声明的 series/rows 精确匹配
2. **字段完整性** — item_ja 100% 非空，values_json 100% 有效 JSON（0 坏 JSON）
3. **category 覆盖** — 画质/尺寸/面板/连接/音频等核心品类命中
4. **row_order 连续性** — 检查断号和重复
5. **captured_week 合法性** — 全库仅合法 ISO 周（YYYY-Www 格式）
6. **item_zh 翻译覆盖率** — jp 线 97~100%，英文站近 0%（流程覆盖差异非损坏）

### 9.2 ★ tcl_jp row_order 结构性重复

tcl_jp 有 28/222 series 存在 row_order 重复（每个 order 值出现 2 次，distinct 后 1..N 连续无断号）。
这是**源站结构特性**（同一规格项双语/双值拆成两行共享 order），非入库损坏。

**处理建议**：若下游依赖 row_order 唯一，对 tcl_jp 加 `(series_id, row_order)` 去重或改用 `(row_order, item_ja)` 联合排序。

### 9.3 ★ item_zh 翻译覆盖两极分化

- jp 品牌（regza_jp/sony_jp/tcl_jp/sharp_jp/panasonic_jp/hisense_jp）：97~100% 翻译覆盖
- uk/us/ca/mx 等英文站：近 0%（item_ja 列对英文站实际存的是英文原文标题）

**注意**：item_ja 列对 uk/us 站存的是英文原文（如 "Screen Size"/"PRICE"），对 jp 站存日文——这是"源语言标题"列的既有设计，非字段名误导。若产品需要中文规范名，应对英文站补跑翻译流程。

### 9.4 ★ SPEC 数据落点必须写主库

SPEC 数据（spec_series + spec_row）必须全部写 `data/overseas.db`，不能写 `data/mx/overseas_mx.db`。详见运维手册「SPEC 线 DB 落点坑」章节。

---

## 十、LangGraph全自动抓取 + DeepResearch报告（2026-09-28 新增 ★）

### 10.1 探索完成后的自动化流程

```
探索接入(人工Skill) → 适配器校准(人工Skill) → 【LangGraph全量抓取(自动)】 → 【DeepResearch报告(自动)】
                                                  FLOW分支                   REPORT分支
```

**LangGraph引擎**（`overseas/flow/`）：7节点StateGraph，SPEC并行度=2，RETAIL并行度=3，每站子进程隔离SQLite，90/600秒超时，品牌熔断+断点续跑。

**DeepResearch报告**（`overseas/report/`）：6-Agent协作（ChiefArchitect→DataScout→DataAnalyst→CompetitorAnalyst→ReportWriter→CriticMaster），所有结论锚定DB真实数据+信源ID，CriticMaster对抗审核检测幻觉，质量评分8.0/10。

### 10.2 新站探索时的防幻觉要点

1. **country字段必须英文大写**：adapter.py的country字段若是中文（如"墨西哥"/"日本"）会导致所有区域分类逻辑失效。新站接入时**必须检查country字段**。
2. **区域分类中英文兼容**：`LINE_COUNTRIES`集合同时包含英文("MEXICO")和中文("墨西哥")，用`countries_upper`大写匹配。
3. **SPEC站product回填**：SPEC站的product表可从`spec_series.models`静态回填，无需联网。size用统一正则`(\d{2,3})`首匹配。

### 10.3 新站探索后的沉淀检查清单

每次新站接入完成，**必须**：
- [ ] `spec/cases.jsonl` 追加一条（格式见文件头注释）
- [ ] `spec/reference.md` 有新规律/坑则更新
- [ ] 有新结构型 → `spec/playbook/` 加套路
- [ ] `spec/selfcheck.py` 跑一次
- [ ] 爬取进度.csv 更新状态+DB数据
- [ ] 如果是SPEC站 → 检查country字段是否英文
