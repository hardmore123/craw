# 套路：规格藏在内嵌 JSON 里

> 状态：✅ 引擎支持（`extract_order` 的 `embedded_json` 档）
> 已验证：Philips CA，约 60 行规格/型号

这份套路只管**规格页解析**（`spec` 段），发现阶段另看对应结构型套路。

---

## 识别信号

跑 `probe.py --model <型号页URL>`，看输出：

| 输出字段 | 判断 |
|---|---|
| `embedded_json_markers` 非空 | 有内嵌 JSON 候选，优先走这档 |
| `generic_spec_js_rows` ≥ 8 | 通用 DOM 抽取已够用，可只用 `generic_spec_js` |
| `generic_spec_js_rows` = 0 且有 markers | **必须**走 `embedded_json` |

常见候选根键名（脚本会扫这些）：
`specification`、`csChapter`、`classifications`、`techSpecs`、`specifications`、`attributeGroups`

---

## 配置模板

```json
"spec": {
  "extract_order": ["embedded_json", "generic_spec_js"],
  "embedded_json": {
    "root_marker": "specification",
    "chapters_field": "csChapter",
    "chapter_name_field": "csChapterName",
    "chapter_code_field": "csChapterCode",
    "items_field": "csItem",
    "item_name_field": "csItemName",
    "values_field": "csValue",
    "value_name_field": "csValueName",
    "value_join": " / "
  }
}
```

**字段全是"页面内嵌 JSON 里的真实键名"**，必须逐个核对，不能照抄 Philips 的。
核对方法：`probe.py --model <url> --save-html` 存下 HTML，搜 `root_marker` 附近的结构。

---

## 三个必须理解的点

### 1. `chapters_field` 兼作 require_field ★

引擎只接受**含 `chapters_field` 的根对象**。

为什么需要：Philips 页面里有多个 `"specification"` 键，只有含 `csChapter` 的那个才是规格数据。
不设这个约束会命中同名但无规格的对象，结果抽出 0 行。

这是历史上真实踩过的坑——当时表现为"手写适配器能抽 60 行，通用引擎抽 0 行"。

### 2. `generic_spec_js` 永远放最后当兜底

```json
"extract_order": ["embedded_json", "generic_spec_js"]
```

按序尝试，第一个出结果即停。内嵌 JSON 字段名万一填错，兜底档还能抢救回一部分。
**不要只写 `["embedded_json"]`。**

### 3. `embedded_json` 与 `embedded_json_philips` 等价

引擎里两个分支走同一个函数：

```python
if method == "embedded_json_philips" or method == "embedded_json":
    triples = _embedded_json_triples(dom.html(), spec_cfg.get("embedded_json") or {})
```

新站统一用 `embedded_json`，`embedded_json_philips` 是历史遗留别名。

---

## 常见失败与修法

| 症状 | 原因 | 修法 |
|---|---|---|
| 规格 0 行，但 markers 检测到了 | 键名填错，或根对象选错 | 存 HTML 逐个核对真实键名 |
| 只抽到几行 | `items_field` / `values_field` 名字对不上 | 核对嵌套层级 |
| 抽到了但内容是导航文字 | `root_marker` 命中了错误对象 | 用更具体的 `chapters_field` 做 require |
| 规格值多个挤在一起 | `value_join` 未设 | 设成 `" / "` |

---

## 需要点 tab 展开才出规格的站

⚠️ **当前引擎无此档位。** TCL 类 SPA 把规格放在需点击的 tab 里，
`generic_spec_js` 抓不到、也没有内嵌 JSON。

遇到这种：记入 `cases.jsonl`，`gap` 字段写"需要交互式 tab 展开解析档"，
走 `needs_handwritten.md`。不要试图用现有档位硬凑。
