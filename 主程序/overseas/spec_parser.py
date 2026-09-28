"""SPEC 表解析器：把官网规格 <table> 解析成 SpecSheet。

海信日本 d6.php 表结构（实测）：
- 单个 <table>，每行一个 SPEC 项。
- th 处理规则：
  * th 只有 1 个 → 项目名；「区分」沿用上方带 rowspan 的分组
  * th 有 2 个 → 第 1 个是新「区分」（带 rowspan 跨 N 行），第 2 个是项目名
  * 首行 th 有 3 个（区分 + シリーズ + 系列名），特殊处理
- td → 各机型的值；全机型同值时可能只有 1 个 td（展开到所有机型）
- 机型顺序取「型番」行的 td

解析不依赖具体品牌的固定行数，靠 rowspan 语义推导「区分」的作用范围，
因此对不同系列（行数不同）都成立。词典翻译在这里一并填入 item_zh。
"""
from __future__ import annotations

import re

from .dict import translate
from .fetchers import Dom
from .spec_items import canonical_item_ja
from .models import SpecCell, SpecRow, SpecSheet

_WS = re.compile(r"[ \t\u3000]+")
# 型番 / 型号 行的项目名（用于定位机型行）
_MODEL_ITEM_KEYS = ("型番", "品番", "型名")


def _clean(s: str) -> str:
    """清理：折叠空白但保留换行语义（换行转 /），去零宽字符。"""
    s = (s or "").replace("\u200e", "").replace("\u200f", "")
    s = s.replace("\r", "").strip()
    # 单元格内换行代表并列项，转成 / 便于单行展示
    s = re.sub(r"\n+", " / ", s)
    return _WS.sub(" ", s).strip(" /")


def _row_th_td(row_html: str) -> tuple[list[tuple[str, int]], list[str]]:
    """从一行 <tr> 的 HTML 里解析 th（带 rowspan）与 td 文本。

    直接用正则而非 DOM，是为了拿到 rowspan 属性（DOM 接口没暴露）。
    返回 ([(th文本, rowspan), ...], [td文本, ...])。
    """
    ths: list[tuple[str, int]] = []
    for m in re.finditer(r"<th\b([^>]*)>(.*?)</th>", row_html, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        rs = re.search(r'rowspan\s*=\s*["\']?(\d+)', attrs, re.I)
        rowspan = int(rs.group(1)) if rs else 1
        ths.append((_clean(_strip_tags(inner)), rowspan))
    tds: list[str] = []
    for m in re.finditer(r"<td\b([^>]*)>(.*?)</td>", row_html, re.S | re.I):
        attrs, inner = m.group(1), m.group(2)
        text = _clean(_strip_tags(inner))
        # td 也可能有 colspan（跨机型同值）
        cs = re.search(r'colspan\s*=\s*["\']?(\d+)', attrs, re.I)
        span = int(cs.group(1)) if cs else 1
        tds.extend([text] * span)
    return ths, tds


def _strip_tags(html: str) -> str:
    # <br> 转换行，其余标签去掉
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    # 常见 HTML 实体
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#215;", "×"), ("&times;", "×")):
        html = html.replace(a, b)
    return html


def parse_hisense_table(table: Dom, brand: str, brand_name: str, series: str,
                        url: str = "") -> SpecSheet:
    """解析海信日本 SPEC 表。返回 SpecSheet（含机型列与逐行数据，已填中文）。"""
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    rows = table.sub("tr", limit=300)

    # rowspan 递减计数：当前「区分」还能覆盖几行
    cur_category = ""
    category_left = 0
    models: list[str] = []
    order = 0
    pending: list[tuple[str, str, list[str]]] = []   # (category, item, td_list)

    for row in rows:
        row_html = row.html()
        ths, tds = _row_th_td(row_html)
        if not ths:
            continue

        # 判定区分与项目名
        if len(ths) >= 2:
            # 第一个 th 是新区分（可能带 rowspan）
            cat_text, cat_span = ths[0]
            cur_category = cat_text
            category_left = max(cat_span, 1)
            item = ths[-1][0]
        else:
            item = ths[0][0]
            if category_left <= 0:
                cur_category = cur_category  # 沿用；无则空
        # 统一已确认的官网异写主行名；值用于补齐“值里才带单位”的包装项。
        item = canonical_item_ja(item, " ".join(tds))

        # 消耗一行区分覆盖
        if category_left > 0:
            category_left -= 1

        # 机型行：td 即机型列表
        if any(k in item for k in _MODEL_ITEM_KEYS):
            models = [t for t in tds if t]
            sheet.models = models

        pending.append((cur_category, item, tds))

    # 第二趟：知道机型数后，把 td 对齐到机型
    n = len(models)
    for cat, item, tds in pending:
        order += 1
        cells: list[SpecCell] = []
        vals = [t for t in tds]
        if not n:
            # 还没拿到机型（异常），用通配
            cells = [SpecCell("*", " / ".join(vals))] if vals else []
        elif len(vals) == 1:
            # 全机型同值
            cells = [SpecCell(m, vals[0]) for m in models]
        else:
            # 逐机型对齐：值不足留空，多余截断（统一用安全索引）
            cells = [SpecCell(models[i], vals[i] if i < len(vals) else "")
                     for i in range(n)]

        sheet.rows.append(SpecRow(
            category=cat, item_ja=item, item_zh=translate(item),
            values=cells, order=order))
    return sheet


def _extract_models_from_kata(text: str) -> list[str]:
    """从索尼「型」行文本里拆机型型号。

    形如 '【K-115XR90M2】115V 【K-85XR90M2】85V ...'，
    取【】里的型号；若无【】则退化为按空白切分。
    """
    hits = re.findall(r"[【\[]([^】\]]+)[】\]]", text or "")
    models = [h.strip() for h in hits if h.strip()]
    if models:
        return models
    # 退化：按空白/顿号切
    parts = re.split(r"[ \u3000/、]+", (text or "").strip())
    return [p for p in parts if p]


def parse_vertical_spec_table(table: Dom, brand: str, brand_name: str, series: str,
                              url: str = "",
                              model_item_keys: tuple[str, ...] = ("型", "型番", "品番", "型名"),
                              category_min_colspan: int = 2) -> SpecSheet:
    """解析「纵向单列」SPEC 表（索尼 bravia spec.html 结构）。

    表结构：
      - 区分行：只有一个 th（通常 colspan>=2/3）且没有 td → 更新当前「区分」分组
      - 项目行：th（项目名）+ 单个 td（全系列共用值）
      - 型号行：项目名匹配 model_item_keys，td 里用【型号】尺寸罗列各机型
    同系列不同尺寸机型共享规格值，故用 SpecCell('*', 值)，导出层展开到各机型列。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    rows = table.sub("tr", limit=400)
    cur_category = ""
    order = 0

    for row in rows:
        ths, tds = _row_th_td(row.html())
        if not ths:
            continue
        # 区分行：一个 th、无 td（或 th 带较大 colspan）
        th_text, th_span = ths[0]
        if len(ths) == 1 and not [t for t in tds if t]:
            # 纯分组行
            if th_text:
                cur_category = th_text
            continue

        item = ths[-1][0]
        vals = [t for t in tds]
        item = canonical_item_ja(item, " ".join(vals))

        # 型号行：只用于拆机型，本身不作为一条 SPEC 项目
        if any(k == item or k in item for k in model_item_keys) and vals:
            raw = " ".join(v for v in vals if v)
            models = _extract_models_from_kata(raw)
            if models:
                sheet.models = models
            continue

        order += 1
        # 全系列共用值 → 通配 '*'
        joined = " / ".join(v for v in vals if v)
        cells = [SpecCell("*", joined)] if joined else []
        sheet.rows.append(SpecRow(
            category=cur_category, item_ja=item, item_zh=translate(item),
            values=cells, order=order))

    # 型号行本身作为一行意义不大，但保留（导出层会当作系列名过滤）
    return sheet


# REGZA spec 页抽取脚本：返回 {models:[...], groups:[{title, rows:[[项目,值],...]}]}
# 结构：多个分组 <table>（2 列 td：项目|值），每表前置最近 h2/h3/h4 为分组标题。
# 型号规律（实测 2026-09）：<尺寸数字><系列码>，如 116ZX1R / 85Z890S / 55X9900R。
# 系列码在 lineup 阶段已知，注入到正则里精确匹配，避免误抓页面其他数字串。

_REGZA_JS_TMPL = r"""
() => {
  const norm = s => (s||'').replace(/\s+/g,' ').replace(/\u00a0/g,' ').trim();
  // 隐藏 tab（display:none）的 innerText 会返回空，必须用 textContent 读取
  const txt = e => norm(e ? (e.textContent||'') : '');
  const SERIES = "__SERIES__";
  const SIZE_RE = /^(\d{2,3})V?型$/;   // 尺寸小标题，如 85V型 / 116V型

  // 1) 尺寸维度：nav.screen-sizes 里按顺序列出的尺寸（85V→75V→…），
  //    与内容区 .content-tab[data-tab="spec-N"] 一一对应（spec-0=第1个尺寸）。
  let navSizes = [...document.querySelectorAll('nav.screen-sizes h2, nav.screen-sizes h3, nav.screen-sizes h4, nav.screen-sizes a, nav.screen-sizes .text-subtitle')]
    .map(e => { const m = txt(e).match(SIZE_RE); return m ? parseInt(m[1]) : null; })
    .filter(n => n !== null);
  navSizes = [...new Set(navSizes)];   // 去重保序

  // 型号 = 尺寸 + 系列码（大写）
  const models = navSizes.map(s => s + SERIES.toUpperCase());

  // 分组标题：table 上溯最近的 h2/h3/h4（不是尺寸小标题）
  const findHeading = t => {
    let cur = t;
    for (let up=0; up<5 && cur; up++) {
      let sib = cur.previousElementSibling;
      while (sib) {
        const cands = [];
        if (sib.matches && sib.matches('h2,h3,h4')) cands.push(sib);
        else if (sib.querySelectorAll) cands.push(...sib.querySelectorAll('h2,h3,h4'));
        for (let k=cands.length-1; k>=0; k--) {
          const tx = txt(cands[k]);
          if (tx && !SIZE_RE.test(tx)) return tx;
        }
        sib = sib.previousElementSibling;
      }
      cur = cur.parentElement;
    }
    return '';
  };
  const readTable = t => {
    const rows = [];
    for (const tr of t.querySelectorAll('tr')) {
      const cells = [...tr.querySelectorAll('th,td')];
      if (cells.length < 2) continue;
      const item = txt(cells[0]);
      const val = txt(cells[1]);
      if (item) rows.push([item, val]);
    }
    return rows;
  };

  // 2) 按 .content-tab 分尺寸段收集分组表。
  //    ti = 表在该 tab 内的序号（每个尺寸 tab 的表结构完全一致），
  //    用 (ti, 行序号) 做跨尺寸对齐锚，避免分组标题在隐藏 tab 里不稳定的问题。
  const tabs = [...document.querySelectorAll('.content-tab')];
  const tables = [];
  const collect = (container, size) => {
    let ti = 0;
    for (const t of container.querySelectorAll('table')) {
      const rows = readTable(t);
      if (rows.length) { tables.push({size, ti, title: findHeading(t), rows}); ti++; }
    }
  };
  if (tabs.length) {
    for (const tb of tabs) {
      const dt = tb.dataset ? (tb.dataset.tab || '') : '';
      const mi = String(dt).match(/(\d+)\s*$/);
      const idx = mi ? parseInt(mi[1]) : 0;
      const size = (idx < navSizes.length) ? navSizes[idx] : (navSizes[0] ?? null);
      collect(tb, size);
    }
  } else {
    collect(document, navSizes.length ? navSizes[0] : null);
  }
  return {models, tables};
}
"""


def regza_extract_js(series_code: str) -> str:
    """按系列码生成 REGZA 抽取脚本（型号正则内联系列码，精确匹配）。"""
    # 系列码是 [A-Z0-9] 安全字符，直接内联到正则；转义为大小写不敏感
    safe = re.sub(r"[^A-Za-z0-9]", "", series_code or "")
    if not safe:
        # 无系列码兜底：宽松匹配 <尺寸><字母数字组合>
        safe = r"[A-Z]{1,3}\d{2,5}[A-Z]?"
    return _REGZA_JS_TMPL.replace("__SERIES__", safe)


# 兼容旧引用：默认脚本用宽松型号正则（无系列码上下文时的兜底）
REGZA_EXTRACT_JS = _REGZA_JS_TMPL.replace("__SERIES__", r"[A-Z]{1,3}\d{2,5}[A-Z]?")


def _model_size(model: str) -> int | None:
    """从型号前缀取尺寸数字，如 85Z890S → 85。"""
    m = re.match(r"(\d{2,3})", model or "")
    return int(m.group(1)) if m else None


def parse_regza_from_data(data: dict, brand: str, brand_name: str, series: str,
                          url: str = "") -> SpecSheet:
    """用 REGZA 抽取数据构建 SpecSheet（按尺寸机型对齐值）。

    REGZA SPEC 页为每个尺寸机型各输出一整套分组表（85V型/75V型…），
    结构为 {models, tables:[{size, title, rows:[[项目,值]]}]}。
    这里把同一 (分组, 项目) 跨尺寸的值对齐成一行：
      - 多机型：值按尺寸分列（SpecCell(机型, 值)），同值时也逐机型展开
      - 单机型 / table 无 size：用 SpecCell('*', 值)，导出层展开到全机型
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    if not data:
        return sheet
    models = [m.strip() for m in (data.get("models") or []) if m and m.strip()]
    sheet.models = models
    # 尺寸 → 机型 映射（用于把 table 的 size 对齐到具体型号）
    size2model: dict[int, str] = {}
    for m in models:
        s = _model_size(m)
        if s is not None:
            size2model.setdefault(s, m)

    tables = data.get("tables")
    if tables is None:
        # 兼容旧字段名 groups（无 size/ti 信息）
        tables = [{"size": None, "ti": gi, **g}
                  for gi, g in enumerate(data.get("groups") or [])]

    # 用 (tab内表序号 ti, 表内行序号 ri) 做跨尺寸对齐锚：每个尺寸 tab 的表结构
    # 完全一致，因此同一 (ti, ri) 在各尺寸下就是同一条 SPEC 项。
    # 分组标题在隐藏 tab 里可能不稳定，故 category/item 取「尺寸最大」那份为准。
    order_keys: list[tuple[int, int]] = []
    label: dict[tuple[int, int], tuple[int, str, str]] = {}   # key -> (best_size, cat, item)
    agg: dict[tuple[int, int], dict[int | None, str]] = {}
    for t in tables:
        size = t.get("size")
        ti = t.get("ti", 0)
        cat = _clean(t.get("title") or "")
        for ri, pair in enumerate(t.get("rows") or []):
            item_ja = _clean(pair[0] if pair else "")
            if not item_ja:
                continue
            val = _clean(pair[1] if len(pair) > 1 else "")
            item_ja = canonical_item_ja(item_ja, val)
            key = (ti, ri)
            if key not in agg:
                agg[key] = {}
                order_keys.append(key)
            if size not in agg[key] or (not agg[key][size] and val):
                agg[key][size] = val
            # 标签取尺寸最大的那份（分组标题最完整）
            sz = size if isinstance(size, int) else -1
            if key not in label or sz > label[key][0]:
                label[key] = (sz, cat, item_ja)

    multi = len(size2model) > 1
    order = 0
    for key in order_keys:
        order += 1
        _, cat, item_ja = label[key]
        by_size = agg[key]
        cells: list[SpecCell] = []
        sized = {s: v for s, v in by_size.items() if isinstance(s, int)}
        if multi and sized:
            for s, model in size2model.items():
                cells.append(SpecCell(model, sized.get(s, "")))
        else:
            v = next((x for x in by_size.values() if x), "")
            if v:
                cells = [SpecCell("*", v)]
        sheet.rows.append(SpecRow(
            category=cat, item_ja=item_ja, item_zh=translate(item_ja),
            values=cells, order=order))
    return sheet


def parse_panasonic_table(table: Dom, brand: str, brand_name: str, series: str,
                          model: str, url: str = "") -> SpecSheet:
    """解析松下 VIERA spec 页的单表（一个型号一页，单机型单值）。

    结构（实测 table.c-prd007__table）：
      - 任意层数的分类 th 带 rowspan，最后一个 th 是项目名，末尾一个 td 是值。
        例：『端子群』(rs=14) 『HDMI端子』(rs=4) 『端子数』(rs=1) | 『4』
      - 用「rowspan th 栈」推导每行的分类路径：栈里保存 (文本, 剩余覆盖行数)，
        每处理一行消耗各层一次，归零则弹出。
    category = 除最后一个 th 外的所有 th 用 / 连接；item = 最后一个 th；值归到 model。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    sheet.models = [model]
    rows = table.sub("tr", limit=400)
    stack: list[list] = []       # [[文本, 剩余行数], ...] 分类层
    order = 0

    for row in rows:
        ths, tds = _row_th_td(row.html())
        if not ths and not tds:
            continue
        # 本行新增的 th：末尾一个是项目名，其余是新分类层（带 rowspan）
        # ths = [(文本, rowspan), ...]
        if not ths:
            continue
        item = ths[-1][0]
        new_cats = ths[:-1]          # 新出现的分类层（本行才开始）
        # 把新分类层压栈（rowspan 决定覆盖多少行，含本行）
        for txt, span in new_cats:
            stack.append([txt, max(span, 1)])

        # 当前分类路径 = 栈里所有层文本
        cat = " / ".join(s[0] for s in stack if s[0])

        vals = [t for t in tds if t]
        val = " / ".join(vals)
        item = canonical_item_ja(item, val)
        order += 1
        cells = [SpecCell(model, val)] if val else []
        sheet.rows.append(SpecRow(
            category=cat, item_ja=item, item_zh=translate(item),
            values=cells, order=order))

        # 本行消耗栈里每一层一次；归零的层弹出（从栈顶往下清理已耗尽的）
        for layer in stack:
            layer[1] -= 1
        while stack and stack[-1][1] <= 0:
            stack.pop()
        # 中间层耗尽也要清理（不只栈顶）
        stack = [l for l in stack if l[1] > 0]

    return sheet


def merge_single_model_sheets(sheets: list[SpecSheet], brand: str, brand_name: str,
                              series: str, url: str = "") -> SpecSheet:
    """把同系列多个「单机型」SpecSheet 合并成一张多机型横排表。

    松下一个系列有多个尺寸型号，各自一页。按 (category, item_ja) 对齐合并，
    机型列按输入顺序排列。用于导出「一系列多机型」的横排 SPEC。
    """
    merged = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    order_keys: list[tuple[str, str]] = []
    agg: dict[tuple[str, str], dict[str, str]] = {}
    zh_map: dict[tuple[str, str], str] = {}
    for sh in sheets:
        for m in sh.models:
            if m not in merged.models:
                merged.models.append(m)
        for r in sh.rows:
            sample_value = " / ".join(c.value for c in r.values if c.value)
            item = canonical_item_ja(r.item_ja, sample_value)
            key = (r.category, item)
            if key not in agg:
                agg[key] = {}
                order_keys.append(key)
                zh_map[key] = translate(item) or r.item_zh
            if not zh_map[key] and r.item_zh:
                zh_map[key] = r.item_zh
            for c in r.values:
                mdl = c.model if c.model not in ("", "*") else (sh.models[0] if sh.models else "")
                if mdl:
                    agg[key][mdl] = c.value
    order = 0
    for cat, item in order_keys:
        order += 1
        cells = [SpecCell(m, agg[(cat, item)].get(m, "")) for m in merged.models]
        merged.rows.append(SpecRow(category=cat, item_ja=item, item_zh=zh_map[(cat, item)],
                                   values=cells, order=order))
    return merged


def _sharp_table_rows(table: Dom, category_hint: str, model: str,
                       order_start: int = 0) -> list[SpecRow]:
    """解析夏普一张 table.table-spec，支持区分 th 的 rowspan。"""
    rows = table.sub("tr", limit=600)
    stack: list[list] = []       # [[分类文本, 剩余覆盖行数], ...]
    out: list[SpecRow] = []
    order = order_start

    for row in rows:
        ths, tds = _row_th_td(row.html())
        if not ths:
            continue
        item = ths[-1][0]
        if not item:
            continue

        # 第一列（或前几列）是带 rowspan 的区分，最后一个 th 是项目。
        for txt, span in ths[:-1]:
            if txt:
                stack.append([txt, max(span, 1)])
        category = " / ".join(x[0] for x in stack if x[0]) or category_hint
        vals = [t for t in tds]
        value = " / ".join(t for t in vals if t)
        item = canonical_item_ja(item, value)
        order += 1
        out.append(SpecRow(
            category=category, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, value)] if value else [], order=order))

        # 本行消耗所有 rowspan；已经结束的分类从栈中移除。
        for layer in stack:
            layer[1] -= 1
        stack = [layer for layer in stack if layer[1] > 0]

    return out


def _sharp_table_kind(table: Dom, index: int) -> str:
    """按表内项目名识别夏普 SPEC 表分组。"""
    names: list[str] = []
    for row in table.sub("tr", limit=600):
        ths, _ = _row_th_td(row.html())
        names.extend(t for t, _ in ths if t)
    joined = " ".join(names)
    if any(k in joined for k in ("画面サイズ", "外形寸法", "本体質量", "梱包サイズ")):
        return "dimensions"
    if any(k in joined for k in ("省エネ基準達成率", "年間消費電力量", "定格消費電力")):
        return "energy"
    return "basic" if index == 0 else "other"


def sharp_extract_models(text: str, series: str = "") -> list[str]:
    """从夏普 SPEC 页面文本中按页面出现顺序提取机型号。"""
    # 日本夏普电视型号实测为 4T-C65GS1、4T-65X7A 等形式。
    candidates = re.findall(r"\b\dT-[A-Z0-9]+(?:-[A-Z0-9]+)?\b", text or "", re.I)
    models: list[str] = []
    for raw in candidates:
        model = raw.upper()
        if series and series.upper() not in model:
            continue
        if model not in models:
            models.append(model)
    # 个别页面正文没有系列码（或后续改版使用不同命名），不因此丢弃型号。
    if not models and series:
        for raw in re.findall(r"\b\dT-[A-Z0-9]+(?:-[A-Z0-9]+)?\b", text or "", re.I):
            model = raw.upper()
            if model not in models:
                models.append(model)
    return models


def parse_sharp_tables(tables: list[Dom], brand: str, brand_name: str,
                       series: str, models: list[str], url: str = "") -> SpecSheet:
    """解析夏普 AQUOS 系列 SPEC 页的多张表。

    页面布局实测为：
      - 第 1 张 table.table-spec：基本规格，各尺寸共用；
      - 若干「省エネ基準達成率」表：按页面型号顺序一表一型号；
      - 若干「寸法・質量」表：按页面型号顺序一表一型号。
    同一（区分、项目）在多个型号表中出现时，先在这里合并成一行，
    避免数据库按（区分、项目、周）保存时覆盖前一个型号的值。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series,
                      url=url, models=list(dict.fromkeys(models)))
    order = 0
    energy_index = 0
    dimensions_index = 0
    merged: dict[tuple[str, str], dict] = {}

    for index, table in enumerate(tables):
        kind = _sharp_table_kind(table, index)
        if kind == "energy":
            model = (sheet.models[energy_index]
                     if energy_index < len(sheet.models) else "*")
            energy_index += 1
            category = "省エネ基準達成率"
        elif kind == "dimensions":
            model = (sheet.models[dimensions_index]
                     if dimensions_index < len(sheet.models) else "*")
            dimensions_index += 1
            category = "寸法・質量"
        elif kind == "basic":
            model = "*"
            category = "基本仕様"
        else:
            model = "*"
            category = "その他"

        parsed = _sharp_table_rows(table, category, model, order)
        order += len(parsed)
        for row in parsed:
            key = (row.category, row.item_ja)
            entry = merged.get(key)
            if entry is None:
                entry = {"category": row.category, "item": row.item_ja,
                         "zh": row.item_zh, "order": row.order, "values": {}}
                merged[key] = entry
            elif not entry["zh"] and row.item_zh:
                entry["zh"] = row.item_zh
            for cell in row.values:
                entry["values"][cell.model] = cell.value

    for entry in merged.values():
        values = entry["values"]
        ordered_models = (["*"] if "*" in values else [])
        ordered_models += [m for m in sheet.models if m in values]
        ordered_models += [m for m in values if m not in ordered_models]
        sheet.rows.append(SpecRow(
            category=entry["category"], item_ja=entry["item"],
            item_zh=entry["zh"],
            values=[SpecCell(m, values[m]) for m in ordered_models],
            order=entry["order"]))
    return sheet


def parse_tcl_spec_json(data: dict, brand: str, brand_name: str, series: str,
                        model: str, url: str = "") -> SpecSheet:
    """解析 TCL 规格 JSON（单型号）。

    结构：{code, msg, data:[{tab:分组名, specItems:[{name:项目, value:值}]}]}
    tab 作为「区分」，specItems 每条一行。单机型单值。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url,
                      models=[model])
    if not isinstance(data, dict):
        return sheet
    order = 0
    for grp in data.get("data") or []:
        cat = _clean(grp.get("tab") or "")
        for it in grp.get("specItems") or []:
            item = _clean(it.get("name") or "")
            if not item:
                continue
            val = _clean(it.get("value") or "")
            item = canonical_item_ja(item, val)
            order += 1
            cells = [SpecCell(model, val)] if val else []
            sheet.rows.append(SpecRow(
                category=cat, item_ja=item, item_zh=translate(item),
                values=cells, order=order))
    return sheet


def collect_untranslated(sheet: SpecSheet) -> list[str]:
    """返回本表中词典未命中、且不是系列名/机型名的项目，供记录待补。"""
    model_set = set(sheet.models)
    out = []
    for r in sheet.rows:
        if r.item_zh:
            continue
        if r.item_ja in model_set or r.item_ja == sheet.series:
            continue
        # 「シリーズ」行的项目名是系列，跳过
        if r.item_ja in ("シリーズ", "系列"):
            continue
        out.append(r.item_ja)
    return out


# ==================== 北美线（英文/西语规格表通用解析）====================


def parse_en_kv_table(table: Dom, brand: str, brand_name: str, series: str,
                      model: str, url: str = "") -> SpecSheet:
    """解析「分组标题行 + 键值行」规格表（Dom table 版，语言无关）。

    结构：整行只有一个非空单元格 → 分组标题(区分)；两个及以上非空单元格 →
    第一个是项目名、其余拼为值。适用于把规格放在 <table> 里的加拿大/墨西哥品牌官网
    （英文/西语标签同样成对解析）。规格若在 dl/dt/dd，改用 parse_en_kv_pairs。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series,
                      url=url, models=[model])
    order = 0
    seen: set[tuple[str, str]] = set()
    cur_category = ""
    for row in table.sub("tr", limit=400):
        ths, tds = _row_th_td(row.html())
        cells = [c.strip() for c in ([t for t, _ in ths] + tds)]
        non_empty = [c for c in cells if c]
        if not non_empty:
            continue
        if len(non_empty) == 1:
            cur_category = non_empty[0]
            continue
        item = non_empty[0]
        value = " / ".join(v for v in non_empty[1:] if v)
        if not item:
            continue
        key = (cur_category, item)
        if key in seen:
            continue
        seen.add(key)
        order += 1
        sheet.rows.append(SpecRow(
            category=cur_category, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, value)] if value else [], order=order))
    return sheet


def parse_en_kv_pairs(triples, brand: str, brand_name: str, series: str,
                      model: str, url: str = "") -> SpecSheet:
    """把 [(区分, 项目, 值), ...] 英文规格三元组解析成单机型 SpecSheet。

    加拿大品牌官网（如 Hisense 的 SECTION.ui-specifications）用「分组标题(H3) +
    dl/dt/dd 键值」组织规格；适配器在页面用 JS 抽出 (category, item, value) 三元组
    交给本函数，与具体 DOM 结构解耦，其它加拿大站可复用同一契约。

    每个型号一页 → 单机型 SpecSheet；同系列多型号由 merge_single_model_sheets 横排合并。
    英文项目名保持原文；item_zh 走 translate()（英文命中不到时留原文），
    加拿大线不强制中文覆盖率（与日本线 xlsx 校验区分）。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series,
                      url=url, models=[model])
    order = 0
    seen: set[tuple[str, str]] = set()
    for triple in triples or []:
        if not triple or len(triple) < 2:
            continue
        category = _clean(str(triple[0] or ""))
        item = _clean(str(triple[1] or ""))
        value = _clean(str(triple[2])) if len(triple) > 2 else ""
        if not item:
            continue
        key = (category, item)
        if key in seen:                 # 同页重复项目只保留首次
            continue
        seen.add(key)
        order += 1
        sheet.rows.append(SpecRow(
            category=category, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, value)] if value else [], order=order))
    return sheet


def parse_flat_pairs(pairs, brand: str, brand_name: str, series: str,
                     model: str, url: str = "", category: str = "") -> SpecSheet:
    """解析「标签, 值, 标签, 值, ...」平铺序列成单机型 SpecSheet。

    TCL 加拿大 Shopify 站的规格区（.specifications_box 内 .specifications_two >
    .specifications_time 交替标签/值）就是这种平铺结构，适配器在页面用 JS 抽出
    一维数组交给本函数。category 可给一个默认区分（TCL 页面规格未分组）。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series,
                      url=url, models=[model])
    flat = [_clean(str(x or "")) for x in (pairs or [])]
    order = 0
    seen: set[str] = set()
    i = 0
    while i + 1 < len(flat):
        item, value = flat[i], flat[i + 1]
        i += 2
        if not item or item in seen:
            continue
        seen.add(item)
        order += 1
        sheet.rows.append(SpecRow(
            category=category, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, value)] if value else [], order=order))
    return sheet


def parse_prefixed_pairs(pairs, brand: str, brand_name: str, series: str,
                         model: str, url: str = "", sep: str = " - ") -> SpecSheet:
    """解析 [(名, 值), ...]，名里用分隔符携带分组前缀的规格对成单机型 SpecSheet。

    LG 加拿大 PDP 的 .c-compare-selling__spec-name/-desc 就是这种：名形如
    'PICTURE (DISPLAY) - Display Type'，分隔符前是区分、后是项目。无分隔符时
    整名作为项目，区分留空。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series,
                      url=url, models=[model])
    order = 0
    seen: set[tuple[str, str]] = set()
    for pair in pairs or []:
        if not pair or len(pair) < 1:
            continue
        raw_name = _clean(str(pair[0] or ""))
        value = _clean(str(pair[1])) if len(pair) > 1 else ""
        if not raw_name:
            continue
        if sep in raw_name:
            category, item = raw_name.split(sep, 1)
            category, item = category.strip(), item.strip()
        else:
            category, item = "", raw_name
        if not item:
            continue
        key = (category, item)
        if key in seen:
            continue
        seen.add(key)
        order += 1
        sheet.rows.append(SpecRow(
            category=category, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, value)] if value else [], order=order))
    return sheet


# 通用「尽力而为」规格抽取 JS：在页面上下文尝试多种常见结构，返回
# [(区分, 项目, 值), ...] 三元组。用于结构多变或懒加载的加拿大零售/品牌站点，
# 抓不到结构化规格时返回空列表（适配器据此降级为仅型号）。
GENERIC_SPEC_JS = r"""() => {
  const out = [];
  const seen = new Set();
  const push = (cat, item, val) => {
    item = (item || '').replace(/\s+/g, ' ').trim();
    val = (val || '').replace(/\s+/g, ' ').trim();
    cat = (cat || '').replace(/\s+/g, ' ').trim();
    if (!item) return;
    const k = cat + '|' + item;
    if (seen.has(k)) return;
    seen.add(k);
    out.push([cat, item, val]);
  };
  // 策略1：dl/dt/dd，取 dl 前最近标题作区分
  document.querySelectorAll('dl').forEach(dl => {
    let cat = '';
    let p = dl.previousElementSibling;
    while (p) { if (/^H[2-5]$/i.test(p.tagName) && (p.textContent||'').trim()) { cat = p.textContent.trim(); break; } p = p.previousElementSibling; }
    const dts = dl.querySelectorAll('dt'), dds = dl.querySelectorAll('dd');
    for (let i = 0; i < dts.length; i++) push(cat, dts[i].textContent, dds[i] ? dds[i].textContent : '');
  });
  // 策略2：name/value 成对容器
  const names = document.querySelectorAll('[class*="spec-name"],[class*="__name"],[class*="specName"]');
  const vals  = document.querySelectorAll('[class*="spec-desc"],[class*="spec-value"],[class*="__value"],[class*="specValue"]');
  for (let i = 0; i < Math.min(names.length, vals.length); i++) push('', names[i].textContent, vals[i].textContent);
  // 策略3：规格表 table，两列 td/th
  document.querySelectorAll('[class*="spec"] table, table[class*="spec"], .specifications table').forEach(t => {
    t.querySelectorAll('tr').forEach(r => {
      const c = r.querySelectorAll('td,th');
      if (c.length >= 2) push('', c[0].textContent, c[1].textContent);
    });
  });
  return out;
}"""
