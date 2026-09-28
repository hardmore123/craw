# 结构型套路索引

按 `probe.py --entry` 给出的信号选套路。**先查这里，别从零想。**

---

## 决策路径

```
probe --entry 拿到 load_more_hint
│
├─ none ──────────┬─ 首屏链接数 ≈ 期望数 ──────→ static_lineup.md      ★覆盖最好
│                 └─ 首屏链接数 << 期望数 ─────→ 有 Load More 但点不开
│                                                 → needs_handwritten.md
├─ scroll ────────────────────────────────────→ lazy_scroll.md
│
├─ click_more_or_paginate ────────────────────→ numbered_paginate.md
├─ paginate_replace ─────────────────────────→ numbered_paginate.md
│
└─ 型号要再进一层系列页才拿到 ────────────────→ multilevel.md      ✅ 可用
   数据来自 JSON 接口而非 DOM ─────────────────→ xhr_api.md         ✅ 可用
```

规格页解析选档单独看：`embedded_json.md`。

---

## 套路清单

| 套路 | 结构特征 | 状态 | 已验证站点 |
|---|---|---|---|
| [static_lineup.md](static_lineup.md) | 首屏一次列全 | ✅ 可用 | 秘鲁 Hisense、REGZA JP、Sony BRAVIA JP |
| [lazy_scroll.md](lazy_scroll.md) | 滚动追加加载 | ✅ 可用 | — |
| [numbered_paginate.md](numbered_paginate.md) | 编号页码分页（含替换式） | ✅ 可用 | Philips CA |
| [embedded_json.md](embedded_json.md) | 规格在内嵌 JSON 里 | ✅ 可用 | Philips CA |
| [needs_handwritten.md](needs_handwritten.md) | 超出通用引擎边界 | — 止损指南 | Hisense US |
| [multilevel.md](multilevel.md) | 总览→系列→型号 多级 | ✅ 可用 | 松下、夏普（待联网实测） |
| [xhr_api.md](xhr_api.md) | JSON 接口出数据 | ✅ 可用 | TCL、Samsung、LG（待联网实测） |

---

## 新增套路的时机

遇到**现有 7 类都套不上**的结构时才新增，不要为单站细节开新套路。
新增时同步更新本索引的决策路径与清单，并在 `cases.jsonl` 里让该站的
`playbook` 字段指向新文件。
