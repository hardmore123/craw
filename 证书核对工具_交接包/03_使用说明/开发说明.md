# 安全件报备清单 × 官网证书核对

按 I 列证书号逐条到 VDE / UL / TÜV 官网查询，把「制造商、生产厂、型号、标准、证书状态」
与表格内容比对，结果写回 M 列。支持多 sheet。

## 现状：三家网站的可抓性

先跑 `python -m cert_verify.cli netcheck`。本机（2026-08）实测结果：

| 机构 | 站点 | 状态 | 说明 |
|---|---|---|---|
| TÜV Rheinland | www.certipedia.com | **可抓，已实现并验证** | 服务端渲染 HTML，纯 HTTP 即可，无需登录 |
| VDE | www.vde.com/tic-en/…/vde-approved-products/search | **已跑通（需开代理/VPN）** | 见下节 |
| UL | productiq.ul.com | **403 + 需登录** | Akamai 反爬；且 Product iQ 要免费账号 |
| IECEE（CB 证书官方库） | certificates.iecee.org | 直连被拦，代理下应可通 | CB 证书本来最该从这里核 |

> **代理/VPN 说明**：直连时 vde.com / iecee.org 被公司网关拦（见下节分析）。
> 开 VPN 或加速器后，本机系统代理（如 `127.0.0.1:7877`）生效，工具会**自动探测并走系统代理**，
> VDE 即可抓通。也可以显式指定：`--proxy http://127.0.0.1:7877` 或设环境变量 `CERT_PROXY`。
> 已实测：开代理后 `query 40052271 --agency VDE` 返回 `found`，数据正确。

### VDE：接口已摸清，解析已对真实响应验证，只剩网络

检索页背后是一个 Solr REST 接口，**免登录、免 Cookie**：

```
GET https://www.vde.com/www8/servlet/restSearch/2289904/zertreg-certificates
    ?wt=json
    &fq=(detailDocType:zertreg|certificate)
    &fq=id:zertreg|certificate#40052271
    &start=0&rows=50
必要请求头：Accept: application/json、Referer: <对应的搜索页>
```

比爬 HTML 好得多：结构化 JSON、一次请求出结果、不用浏览器。字段映射基于证书号
40052271 的真实响应验证过：

| JSON 字段 | 映射到 | 实际值 |
|---|---|---|
| `companyName` / `_en` / `_de` | 制造商 | `Xiamen Wanming` |
| `typeNames`（数组，完整型号表） | 型号 | 10 条，含 `*` 通配 |
| `firstTypes` | 型号兜底（截断预览串） | — |
| `id` = `zertreg\|certificate#40052271` | 证书号 | `40052271` |
| `registerNumber` | VDE 注册号 Reg.-Nr. | `E809` |
| `productName` / `_en` | 产品类别 | `Varistor` |
| `validityLimit` | 到期日 | 本例为空 |
| `numFound >= 1` | 证书状态 | 见下 |

**证书状态怎么判**：服务端在 `fq` 里强制加了 `validfrom:[* TO NOW/MINUTE]` 和
`validto:[NOW/MINUTE TO *]`，即**接口只返回当前有效的证书**，命中即「有效」。
代价是查不到时分不清「不存在」还是「已失效」，工具如实写「官网未查到（可能已失效或
证书号有误）」，不猜。想区分可把 `vde.py` 的 `INCLUDE_EXPIRED` 改 `True`
（会带上 `validfrom=[* TO *]`），服务端是否允许覆盖需联网实测。

**标准和生产厂**：接口默认 `fl` 里没有这两项。代码先试 `fl=*` 取全字段，
取不到就留空并记「网站无此项」，不判不一致。联网后跑
`python -m cert_verify.cli probe-vde --cert-no 40052271`
会把原始 JSON 落盘，一看便知 `fl=*` 是否生效；若确实没有，得另找详情接口。

**型号通配**：VDE 用 `WMR05D561K *`、`WMR05D180M * bis/to` 表示同系列/范围。
`compare.py` 会把 `*` 编译成通配正则、把 `bis/to` 当噪音剔除，所以表格写
`WMR10D112K35` 也能和网站的 `WMR10D112K *` 对上。

**仍然卡在网络。为什么浏览器能打开、脚本不行？** 跑 `python -m cert_verify.cli netdiag`
分层看，结论很清楚：

| 层 | vde.com 的表现 | 对照：certipedia.com |
|---|---|---|
| DNS | 91.105.243.113 | 195.20.221.239 |
| TCP 443 | **连上，251 ms** | 连上，216 ms |
| TLS 校验 | 证书不受信任（拿不到签发者） | **OK**，签发者 D-TRUST（真实公网 CA）|
| TLS 不校验 | **握手超时** | OK |

TCP 通、TLS 收到一张不受信任的证书、关掉校验后握手却卡死 —— 这是网关做 SSL 审查
但**不放行上游**的典型特征：它先回自己的证书，然后就不再往下走。`curl.exe`
（走 Windows Schannel）同样报 `SEC_E_UNTRUSTED_ROOT`，无头 Edge 报 `ERR_TIMED_OUT`。
certipedia 用同一个 Python 直接验证通过，说明**不是本机信任库的问题**，是 vde.com
被专门拦。

**浏览器能打开是因为它有自己的出口**：Edge 里装了 `iGG谷歌学术助手`
（扩展 ID `mchibleoefileemjfghfejaggonplmmg`，权限含 `proxy` + `webRequest`，
host 权限 `*://*/*`）。这类扩展用 Chrome 的 proxy API **只代理浏览器内的流量**，
Python / curl / 系统网络栈都不经过它。系统代理是关的（`ProxyEnable=0`）、
没有 PAC、常见代理端口无监听、单网卡默认网关 10.18.76.1 —— 系统层面没有可用出口。

所以直连时两条路径的出口不同，不是改代码能绕的。

**开 VPN/代理后已跑通**：开加速器后系统代理生效（`ProxyEnable=1`，`127.0.0.1:7877`），
这时脚本走这个系统代理就能穿透网关。工具**默认自动探测系统代理**，无需额外配置；
也可 `--proxy http://127.0.0.1:7877` 或设 `CERT_PROXY` 显式指定。实测：

```
> python -m cert_verify.cli query 40052271 --agency VDE
status=found  manufacturer=Xiamen Wanming  registerNumber=E809  型号 10 条全取到

> python -m cert_verify.cli netdiag --host www.vde.com
TLS不校验 TimeoutError
经代理    成功 HTTP 200 （http://127.0.0.1:7877）
结论      直连被网关拦，但经代理可通
```

注意：iGG 是浏览器扩展代理，只有开了会挂系统代理的 VPN/加速器时脚本才借得到；
纯扩展形态（不改系统代理）脚本仍走不了。

**离线兜底**：没有代理时，在能上网的机器上 F12 → Network → 过滤 `restSearch` → Copy response，
存成 json 后：

```bat
python -m cert_verify.cli parse-file vde 保存的响应.json
```

解析规则与在线路径完全一致。`tests\fixtures\vde_40052271.json` 就是这么来的，
`tests\test_vde_parse.py` 拿它做了 20 项断言，全部通过。

结论：**TÜV 这条线现在就能跑全量；VDE / UL 卡在网络和账号，不是代码问题。**
要打通得二选一：
1. 让 IT 把 `vde.com`、`productiq.ul.com`、`certificates.iecee.org` 加进白名单；
2. 或配代理：`set VDE_PROXY=http://127.0.0.1:7890`（`UL_PROXY` 同理）。

## 安装

```bat
pip install -r cert_verify/requirements.txt
:: 只有要抓 VDE / UL 才需要
pip install playwright && python -m playwright install chromium
```

## 用法

```bat
:: 0. 网络自检，先看哪几家能抓
python -m cert_verify.cli netcheck

:: 1. 单条试抓，确认字段解析对
python -m cert_verify.cli query 50679826 --agency TUV

:: 2. 看表格结构，确认列映射
python -m cert_verify.cli inspect "电源电路部分安全件报备清单明细表_认证类别UL-CB 20230420.xlsx"

:: 3. 先小样 10 条跑通，再全量
python -m cert_verify.cli run "xxx.xlsx" --limit 10
python -m cert_verify.cli run "xxx.xlsx" --detail-col N

:: 端到端自测（造样例表 + 真实抓取 + 比对 + 写回）
python -m cert_verify.selftest

:: VDE 解析器单测（不依赖网络）
python cert_verify\tests\test_vde_parse.py
```

`run` 不会改原文件，输出到 `原名_核对结果.xlsx`。

## 配置列映射

题目只给了 I 列（证书号）和 M 列（结果），其余五个字段所在列要照实际表填。
先 `inspect` 打印表头，再改 `config.py`：

```python
SHEET_COLUMNS = {
    "UL":  ColumnMap(cert_no="I", result="M", manufacturer="C", factory="D",
                     model="E", standard="F", cert_status="J", agency="G",
                     first_data_row=3),
    "CB":  ColumnMap(...),   # 两个 sheet 列结构不同就分别配
}
```

`agency` 列（认证机构）建议配上；没有的话工具会按证书号形态猜，
也可以用 `--agency TUV` 给整个 sheet 指定默认机构。

## 代码结构

```
cert_verify/
  cli.py            命令行入口
  netcheck.py       网站可达性自检（抓前必跑）
  http_client.py    标准库 HTTP：重试 / 限速 / 磁盘缓存 / 识别防火墙拦截页
  browser_client.py Playwright 封装（VDE / UL 用，可选依赖）
  models.py         CertRecord：统一的证书字段模型
  providers/
    tuv_rheinland.py  Certipedia，纯 HTTP，已验证
    vde.py            Playwright 驱动官方查询页，等网络放通
    ul.py             Product iQ，需账号 + 网络放通
    __init__.py       ProviderRouter：按机构名/证书号形态路由
  compare.py        归一化 + 五字段比对
  excel_runner.py   读 xlsx → 查询 → 比对 → 写 M 列
  config.py         列映射与运行参数
```

## TÜV Rheinland 抓取路径（已实测）

```
证书详情：GET https://www.certipedia.com/certificates/<数字证书号>?locale=en
搜索兜底：GET https://www.certipedia.com/search/matching_product_certificates?locale=en&q=<证书号>
```

页面字段 → 模型映射：

| Certipedia 标签 | 映射到 |
|---|---|
| Certificate Holder | 制造商 |
| Certified Product → `Model Designation:` | 型号 |
| Fulfilled Standards | 标准 |
| `<h2 class="certificate-status">` | 证书状态（Valid / Invalid Certificate）|
| Certificate Number / Date of Issue / Certificate Type | 辅助信息 |

**注意：Certipedia 不公开「生产厂 / Manufacturing plant」。**
该字段网站取不到，工具记「网站无此项」，不判为不一致 —— 生产厂只能靠证书 PDF 或人工核。

## 比对怎么避免误报

抓取不难，难在「同一个意思写法不同」。compare.py 分三层处理：

- **归一化**：全角半角、大小写、标点、公司后缀（`Co.,Ltd.` / `CO LTD` / `GmbH` 一律忽略）。
- **分字段策略**
  - 制造商 / 生产厂：归一化后包含判定 + 相似度 ≥0.86 算一致（网站字段常带地址）。
  - 型号：先剥掉括号里的品牌备注，再按 `/ , 、 ;` 切成集合，判交集；单个再做 0.9 相似度兜底。
  - 标准：抽成标准号集合，`EN IEC 62368-1` 归一为 `EN 62368-1`；完全命中算一致，
    只差年份/A修订则算一致但在 M 列注明「版本/年份不同」，交人工判断。
  - 状态：映射成「有效 / 失效」两态再比。
- **区分四种情况**：一致 / 不一致 / 网站无此项 / 表格未填。只有真「不一致」才标 ✗。

已知比不了的一类：**中英文混写**。表格写「东莞」、VDE 写 `Dongguan, China`，
字符串比对必然判不一致。这类只能标出来人工看，不要指望工具自动过。

M 列输出示例：

```
✓ 一致：制造商、型号、标准、证书状态；（生产厂网站无此项）
✓ 一致：制造商、型号、标准(版本/年份不同: 表格=['en62368-1:2014'] 网站=['en62368-1:2020+a11'])、证书状态
✗ 不一致：型号不一致[表格=A24-999XXX | 网站=A24-065N3A]
✗ 不一致：证书状态不一致[表格=有效 | 网站=Invalid Certificate]
⚠ 官网未查到该证书号（TUV-Rheinland）
✗ 网络被拦截，未能查询（VDE）：vde.com 被公司网络 URL 过滤拦截
```

## 抓取礼貌与合规

- 默认单站请求间隔 1.2s + 随机抖动，HTML 落磁盘缓存（`.cache/`），重跑不重复打站。
- UL Product iQ 批量查询建议走 UL 官方 API / 数据授权，而不是爬网页；
  这里的网页方式只适合几十~几百条的人工核对量级。
- 一旦检测到防火墙拦截页，默认立刻停止（`stop_on_blocked`），避免把整表写满错误结论。
