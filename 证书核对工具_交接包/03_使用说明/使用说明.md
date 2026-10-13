# 安全件报备清单 × 官网证书核对工具

按表格 I 列的证书号逐条到认证机构官网查询，把「制造商、生产厂、型号、标准、证书状态」
和表格内容比对，结论写回 M 列。支持一个文件里多个 sheet。
原文件不动，结果另存为 `原名_核对结果.xlsx`。

---

## 零、网页版（推荐，最简单）

双击 **`启动网页.bat`**，浏览器会自动打开 `http://127.0.0.1:8756`。整个流程在网页里点：

1. **上传**报备清单（.xlsx）→ 自动列出工作表和表头预览。
2. **确认列映射**：勾选要核对的工作表，填各字段所在列（证书号默认 I、结果默认 M；
   制造商/生产厂/型号/标准/证书状态/机构列按实际填，留空表示表格没这列）。
   可设「限制条数」先小样、代理、详情列、被拦截即停。
3. 点**开始核对**：实时看进度（当前工作表 / 证书号 / 机构）和统计
   （查到 / 未查到 / 被拦截 / 全一致 / 需复核），可随时**取消**。
4. **结果表**：点任意行看字段级明细（表格值 vs 官网值）；可筛选「仅不一致」
   「仅未查到/被拦截」。点**下载结果 Excel** 得到副本：
   - **真正不一致的字段单元格用红色字体标注**；
   - M 列写结论摘要（含官网正确值），N 列写来源 URL；
   - 原文件不动，保留原格式。

要点：
- 服务只绑本机 `127.0.0.1`，不对外网开放，无需登录。
- 查 **VDE** 需先开 VPN/加速器（工具自动走系统代理）。
- 查 **UL** 需在网页上点「UL 登录」，会弹出本机 Edge，登录一次即可（登录态存本地，不外泄）。
- 端口被占用会自动换到 8757、8758…（看控制台打印的实际地址）。

下面第一~七节是命令行/批处理用法，网页版用不到也可以只看第零节。

---

## 一、现在哪些网站能抓，哪些不能

跑 `2_网络自检.bat` 会打印实时结果。截至打包时的实测情况：

| 机构 | 站点 | 能否抓取 | 卡在哪 |
|---|---|---|---|
| **TÜV Rheinland** | www.certipedia.com | **能，已验证可用** | — |
| VDE | www.vde.com（restSearch JSON 接口） | **已跑通（需开代理/VPN）** | 直连被网关拦，走代理即通 |
| UL Product iQ | productiq.ul.com | 不能 | 反爬 403 + 需登录账号 |
| UL Prospector | productiq.ulprospector.com | 需开代理/VPN | 直连被网关拦 |
| IECEE（CB 证书官方库） | certificates.iecee.org | 需开代理/VPN | 直连被网关拦 |
| TÜV SÜD | www.tuvsud.com | 不能 | 反爬 403 |

> **开代理/VPN 后 VDE 已实测跑通。** 开加速器后本机系统代理生效
> （如 `127.0.0.1:7877`），工具会**自动探测并走系统代理**，无需额外配置；
> 也可 `--proxy http://127.0.0.1:7877` 或设环境变量 `CERT_PROXY` 显式指定。
> 实测 `query 40052271 --agency VDE` 返回 `found`，数据正确。

### 两类问题，性质完全不同

**第一类：公司网络白名单（VDE / UL Prospector / IECEE）**

这是外部网络策略问题，跟程序无关。

### 为什么浏览器能打开、脚本却不行？

这一点最容易误判，所以专门做了分层诊断（`2_网络自检.bat` 会跑）：

| 层 | vde.com | 对照：certipedia.com |
|---|---|---|
| DNS | 91.105.243.113 | 195.20.221.239 |
| TCP 443 | **连上，251 ms** | 连上，216 ms |
| TLS 校验 | 证书不受信任 | **OK**，签发者 D-TRUST（真实公网 CA）|
| TLS 不校验 | **握手超时** | OK |

TCP 通、TLS 收到一张不受信任的证书、关掉校验后握手反而卡死 —— 这是网关做 SSL 审查
但**不放行上游**的典型特征：先回自己的证书，然后不再往下走。旁证：

- `curl.exe`（走 Windows 证书库）→ `SEC_E_UNTRUSTED_ROOT`
- 无头 Edge（全新配置）→ `ERR_TIMED_OUT`
- `http://www.vde.com/...` → 302 到 `http://10.18.0.250/disable/disable.htm`，标题「URL过滤」
- 而 certipedia 用同一个 Python 直接验证通过 → **不是本机信任库的问题**

**浏览器能打开，是因为它有自己的出口。** Edge 里装了 `iGG谷歌学术助手`
（扩展 ID `mchibleoefileemjfghfejaggonplmmg`，权限含 `proxy`、`webRequest`，
host 权限 `*://*/*`）。这类扩展通过浏览器的 proxy API **只代理浏览器内的流量**，
Python、curl、系统网络栈都不经过它。系统层面确认没有可用出口：系统代理是关的
（`ProxyEnable=0`）、无 PAC、常见代理端口无监听、单网卡默认网关 10.18.76.1。

所以直连时「F12 能拿到」和「脚本能拿到」走的是两条不同的出口。要让脚本也通，三选一：

1. **开 VPN/加速器（已验证可行）**。开了之后系统代理会打开（`ProxyEnable=1`、
   `127.0.0.1:7877`），工具**自动探测并走系统代理**，VDE 直接抓通。
   也可显式 `--proxy http://127.0.0.1:7877` 或设 `set CERT_PROXY=http://127.0.0.1:7877`。
   注意：iGG 是浏览器扩展，只有开了会改系统代理的 VPN 时脚本才借得到；
   纯扩展形态（不改系统代理）脚本走不了。
2. **让 IT 加白名单**（长期最干净）：
   `vde.com`、`certificates.iecee.org`、`productiq.ul.com`、`productiq.ulprospector.com`
3. **过渡办法**：浏览器里手工取 JSON，用 `parse-file` 解析（见后文）。几十条内可行。

顺带一提：清单是 UL-CB 类别，**CB 证书最权威的核对源是 IECEE 官方库**，
报白名单时务必把 `certificates.iecee.org` 一起报上去。

**第二类：反爬 + 需登录（UL Product iQ / TÜV SÜD）**

即使网络放通，UL Product iQ 还要免费账号，且有 Akamai 反爬，得用真实浏览器
（Playwright）驱动。批量查询建议走 UL 官方 API 或数据授权，别爬网页；
网页方式只适合几十~几百条的人工核对量级。

### VDE：代码已经写完并验证，只等网络放通

VDE 检索页背后是一个 Solr REST 接口，**免登录、免 Cookie**：

```
GET https://www.vde.com/www8/servlet/restSearch/2289904/zertreg-certificates
    ?wt=json
    &fq=(detailDocType:zertreg|certificate)
    &fq=id:zertreg|certificate#40052271
    &start=0&rows=50
```

只需两个请求头：`Accept: application/json` 和 `Referer`（指向对应搜索页）。
返回结构化 JSON，比爬 HTML 稳得多，也不需要浏览器。

解析逻辑是**对着证书号 40052271 的真实响应写的**，字段映射：

| JSON 字段 | 映射到 | 实际值 |
|---|---|---|
| `companyName` / `_en` / `_de` | 制造商 | `Xiamen Wanming` |
| `typeNames`（完整型号数组） | 型号 | 10 条，含 `*` 通配 |
| `id` = `zertreg\|certificate#40052271` | 证书号 | `40052271` |
| `registerNumber` | VDE 注册号 | `E809` |
| `productName_en` | 产品类别 | `Varistor` |
| `validityLimit` | 到期日 | — |
| `numFound >= 1` | 证书状态 | 有效 |

真实响应存在 `cert_verify\tests\fixtures\vde_40052271.json`，
`9_自测.bat` 会拿它跑 20 项断言，**当前全部通过**。所以网络一通就能直接用，
不需要再改解析代码。

### 三个要注意的点

**证书状态是推断出来的。** 服务端强制加了有效期过滤（`validfrom`/`validto`），
即这个接口**只返回当前有效的证书**，所以命中就是「有效」。代价是查不到时分不清
「证书不存在」还是「已失效」，工具会如实写「官网未查到（可能已失效或证书号有误）」，
不替你猜。

**标准和生产厂接口默认不给。** 代码会先试 `fl=*` 把全字段取回来；取不到就留空并记
「网站无此项」，不判不一致。网络通了先跑一次：

```bat
python -m cert_verify.cli probe-vde --cert-no 40052271
```

原始 JSON 会落盘，一看就知道 `fl=*` 有没有生效、有没有标准字段。

**VDE 型号带通配符。** 官网写 `WMR05D561K *`、`WMR05D180M * bis/to` 表示同系列或范围。
工具会把 `*` 当通配、`bis/to` 当噪音处理，所以表格写具体型号 `WMR10D112K35`
也能和官网的 `WMR10D112K *` 对上，不会误报不一致。

---

## 二、怎么用

### 第一次使用

1. 双击 `1_安装依赖.bat`（只需一次，装 openpyxl）
2. 双击 `2_网络自检.bat`，确认 Certipedia 显示「可达」
3. 双击 `9_自测（验证程序本身）.bat`，确认程序本身没问题

### 正式核对

4. 把 xlsx **拖到** `3_查看表格列结构.bat` 上，看清各列是什么
5. 用记事本打开 `cert_verify\config.py`，把列字母填进 `SHEET_COLUMNS`：

```python
SHEET_COLUMNS = {
    "你的sheet名": ColumnMap(
        cert_no="I",        # 证书号（已固定）
        result="M",         # 结果写这里（已固定）
        manufacturer="C",   # 制造商在哪列
        factory="D",        # 生产厂
        model="E",          # 型号
        standard="F",       # 标准
        cert_status="J",    # 证书状态
        agency="G",         # 认证机构（VDE/UL/TUV），没有这列就留空
        first_data_row=2,   # 数据从第几行开始
    ),
}
```

两个 sheet 列结构不一样就分别写两条。没有「认证机构」列的话，
可以在命令里加 `--agency TUV` 给整个表指定默认机构。

6. 把 xlsx 拖到 `4_试跑10条.bat` 上，先看 10 条结果对不对
7. 没问题了，拖到 `5_全量核对.bat` 上跑完

---

## 三、M 列会写什么

```
✓ 一致：制造商、型号、标准、证书状态；（生产厂网站无此项）
✓ 一致：制造商、型号、标准(版本/年份不同: 表格=en62368-1:2014 网站=en62368-1:2020+a11)、证书状态
✗ 不一致：型号不一致[表格=A24-999XXX | 网站=A24-065N3A]
✗ 不一致：证书状态不一致[表格=有效 | 网站=Invalid Certificate]
⚠ 官网未查到该证书号（TUV-Rheinland）
✗ 网络被拦截，未能查询（VDE）：vde.com 被公司网络 URL 过滤拦截
```

加 `--detail-col N` 会把来源 URL 写到 N 列，方便点开人工复核。

---

## 四、几个必须知道的限制

**1. TÜV Rheinland 不公开「生产厂」**

Certipedia 上没有 Manufacturing plant 这一项。工具会记「网站无此项」而不判不一致 ——
生产厂只能靠证书 PDF 或人工核，这点绕不过去。

**2. 中英文混写比不了**

表格写「东莞」、网站写 `Dongguan, China`，字符串比对必然判不一致。
这类会被标出来，需要人工看，别指望工具自动过。

**3. 标准差年份算「一致」但会注明**

`EN 62368-1:2014` vs `EN IEC 62368-1:2020+A11` 判为一致并在 M 列注明「版本/年份不同」。
是否算问题由你判断 —— 工具不替你做这个决定。

**4. 结论只有「不一致」才是 ✗**

工具区分四种情况：一致 / 不一致 / 网站无此项 / 表格未填。
「网站无此项」和「表格未填」都不算不一致，避免刷出一堆假警报。

**5. 一旦检测到防火墙拦截页会立刻停止**

避免把整张表写满「查询失败」这种没用的结论。

---

## 五、抓取礼貌

- 单站请求间隔 1.2 秒 + 随机抖动
- HTML 落磁盘缓存（`cert_verify\.cache\`），重跑不重复打人家的站
- 缓存目录可以随时删

---

## 六、目录结构

```
证书核对工具\
  1_安装依赖.bat
  2_网络自检.bat
  3_查看表格列结构.bat
  4_试跑10条.bat
  5_全量核对.bat
  9_自测（验证程序本身）.bat
  使用说明.md
  网络现状快照.txt
  cert_verify\
    cli.py              命令行入口
    netcheck.py         网站可达性自检
    http_client.py      HTTP：重试 / 限速 / 缓存 / 识别防火墙拦截页
    browser_client.py   Playwright 封装（VDE/UL 用，可选依赖）
    models.py           统一的证书字段模型
    compare.py          归一化 + 五字段比对
    excel_runner.py     读 xlsx → 查询 → 比对 → 写 M 列
    config.py           列映射配置（要改的就是这个）
    selftest.py         端到端自测
    providers\
      tuv_rheinland.py  Certipedia，已验证
      vde.py            等网络放通
      ul.py             等网络放通 + 账号
```

## 七、命令行（不想用 bat 的话）

```bat
python -m cert_verify.cli netcheck
python -m cert_verify.cli query 50679826 --agency TUV
python -m cert_verify.cli inspect "清单.xlsx"
python -m cert_verify.cli run "清单.xlsx" --limit 10 --detail-col N
python -m cert_verify.cli run "清单.xlsx" --sheet UL --sheet CB
python -m cert_verify.cli probe-vde --cert-no 40052271
python -m cert_verify.cli parse-file vde 保存的响应.json
```

最后一条是离线兜底。VDE 网络没放通时，在能上网的机器上按
**F12 → Network → 过滤 `restSearch` → 右键 Copy response**，存成 json 再喂给解析器，
规则和在线路径完全一致。
