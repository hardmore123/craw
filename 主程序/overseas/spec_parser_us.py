"""美国线 SPEC 解析辅助。

美国各品牌官网的规格不是 <table>，而是 div 键值块（TCL）、内嵌 JSON
（Sony/Samsung/LG）或 Wix 组件（Hisense）。各适配器负责把原始结构规整成
统一的 [(分组, 项目, 值), ...] 三元组，再由本模块构建单机型 SpecSheet；
多个尺寸型号的单机型表最终由 spec_parser.merge_single_model_sheets 横排合并，
产出与日本线一致的「区分 | 项目 | 项目(中文) | 机型1 | 机型2 ...」表。

翻译沿用离线词典 dict.translate（默认不联网）；英文项目多数词典未命中，
item_zh 留空，符合「先不接翻译/大模型」的要求，后续补词典即可回填。
"""
from __future__ import annotations

import re

from .dict import translate
from .spec_items import canonical_item_ja
from .models import SpecCell, SpecRow, SpecSheet

_WS = re.compile(r"[ \t\u3000]+")


def clean_text(s: str) -> str:
    """清理规格文本：去零宽字符、折叠空白、换行转 /。"""
    s = (s or "").replace("\u200e", "").replace("\u200f", "")
    s = s.replace("\r", "").replace("\xa0", " ").strip()
    s = re.sub(r"\n+", " / ", s)
    s = _WS.sub(" ", s).strip(" /")
    return s


def build_single_model_sheet(
    brand: str,
    brand_name: str,
    series: str,
    model: str,
    triples: list[tuple[str, str, str]],
    url: str = "",
) -> SpecSheet:
    """用 [(分组, 项目, 值), ...] 构建单机型 SpecSheet。

    - 空项目名或空值的行跳过（值全空的项目对导出无意义）。
    - 同一 (分组, 项目) 若重复出现，后值以 / 追加，避免丢信息。
    - item 经 canonical_item_ja 归一，保证与合并/导出一致。
    """
    sheet = SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    sheet.models = [model] if model else []
    seen: dict[tuple[str, str], int] = {}   # (cat,item) -> rows 下标
    order = 0
    for cat, item, val in triples:
        cat = clean_text(cat)
        val = clean_text(val)
        item = canonical_item_ja(clean_text(item), val)
        if not item or not val:
            continue
        key = (cat, item)
        if key in seen:
            row = sheet.rows[seen[key]]
            if row.values and val not in row.values[0].value.split(" / "):
                row.values[0] = SpecCell(model, row.values[0].value + " / " + val)
            continue
        order += 1
        seen[key] = len(sheet.rows)
        sheet.rows.append(SpecRow(
            category=cat, item_ja=item, item_zh=translate(item),
            values=[SpecCell(model, val)] if model else [SpecCell("*", val)],
            order=order))
    return sheet


# ==================== TCL 美国（Shopify，div 键值块）====================
# 结构（实测 2026-09）：一个产品页含该系列所有尺寸型号，每型号一段：
#   <div class="specifications_box">
#     <div class="specifications_li specifications_one"><div class="h3">分组标题</div></div>
#     <div class="specifications_li specifications_two">
#        <div class="specifications_time">key</div><div class="specifications_time">val</div> ...
#     </div>
#   </div>
# 型号分隔靠特殊分组标题「TCL <系列> Class - <MODEL>」（其数据段为空或列信息）。

_TCL_MODEL_TITLE = re.compile(r"-\s*([0-9]{2,3}[A-Za-z0-9]+)\s*$")
# 按文档顺序抓取每个 specifications_li 块：one=标题(分组或型号) / two=键值区
_TCL_LI = re.compile(
    r'specifications_li specifications_(one|two)"\s*>(.*?)(?=<div class="specifications_li|'
    r'<div class="specifications_box|<div class="variant-specs-panel|$)', re.S)
_TCL_H3 = re.compile(r'<div class="h3">(.*?)</div>', re.S)
_TCL_TIME = re.compile(r'<div class="specifications_time">(.*?)</div>', re.S)


def _unescape(s: str) -> str:
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                 ("&#215;", "×"), ("&times;", "×"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&apos;", "'")):
        s = s.replace(a, b)
    return re.sub(r"<[^>]+>", " ", s)


def parse_tcl_us_html(html: str, brand: str, brand_name: str, series: str,
                      url: str = "") -> SpecSheet:
    """解析 TCL 美国产品页 HTML，产出该系列多机型横排 SpecSheet。

    按「... - <MODEL>」分组标题把规格块切成多个型号段，每段内解析分组键值，
    最终把各型号表按 (分组, 项目) 对齐合并成横排表。
    """
    from .spec_parser import merge_single_model_sheets

    seg_model = ""
    seg_group = ""
    per_model: dict[str, list[tuple[str, str, str]]] = {}
    model_order: list[str] = []

    for kind, chunk in _TCL_LI.findall(html or ""):
        if kind == "one":
            mh3 = _TCL_H3.search(chunk)
            title = clean_text(_unescape(mh3.group(1))) if mh3 else ""
            mm = _TCL_MODEL_TITLE.search(title) if title else None
            if mm:
                seg_model = mm.group(1).upper()
                seg_group = ""
                if seg_model not in per_model:
                    per_model[seg_model] = []
                    model_order.append(seg_model)
            elif title:
                seg_group = title
            continue
        # kind == "two"：键值区
        if not seg_model:
            continue
        times = [clean_text(_unescape(t)) for t in _TCL_TIME.findall(chunk)]
        for i in range(0, len(times) - 1, 2):
            k, v = times[i], times[i + 1]
            if k and k.lower() != "model":     # Model 行已作为机型列，无需作项目
                per_model[seg_model].append((seg_group, k, v))

    if not per_model:
        return SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)

    sheets = [
        build_single_model_sheet(brand, brand_name, series, m, per_model[m], url)
        for m in model_order if per_model[m]
    ]
    if not sheets:
        return SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    merged = merge_single_model_sheets(sheets, brand, brand_name, series, url)
    return merged


# ==================== 通用：从 HTML 内嵌大 JSON 中按配平提取片段 ====================
import json as _json


def extract_balanced(text: str, key: str, start_from: int = 0):
    """从 text 中找到 "<key>":[...] 或 "<key>":{...}，按括号配平返回解析后的对象。

    用于从 SPA hydration 大 JSON 里摘出某个子结构（如 Sony 的 classifications）。
    找不到或解析失败返回 None。
    """
    marker = f'"{key}"'
    i = text.find(marker, start_from)
    if i < 0:
        return None
    j = text.find(":", i + len(marker))
    if j < 0:
        return None
    k = j + 1
    while k < len(text) and text[k] in " \t\r\n":
        k += 1
    if k >= len(text) or text[k] not in "[{":
        return None
    open_ch = text[k]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    in_str = False
    esc = False
    for pos in range(k, len(text)):
        c = text[pos]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                frag = text[k:pos + 1]
                try:
                    return _json.loads(frag)
                except Exception:
                    return None
    return None


# ==================== Sony 美国（内嵌 JSON classifications）====================
# 结构：产品状态 JSON 里 "classifications":[{code,name,features:[
#   {code,name,featureValues:[{value}]}]}]；分组 name 作区分，feature name 作项目，
#   featureValues[].value 拼接作值。


def parse_sony_us_html(html: str, brand: str, brand_name: str, series: str,
                       model: str, url: str = "") -> SpecSheet:
    classifications = extract_balanced(html or "", "classifications")
    if not isinstance(classifications, list):
        return SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    triples: list[tuple[str, str, str]] = []
    for grp in classifications:
        if not isinstance(grp, dict):
            continue
        gname = str(grp.get("name") or grp.get("code") or "")
        for feat in grp.get("features") or []:
            if not isinstance(feat, dict):
                continue
            fname = str(feat.get("name") or "")
            vals = []
            for fv in feat.get("featureValues") or []:
                if isinstance(fv, dict) and fv.get("value") is not None:
                    vals.append(str(fv["value"]))
            val = " / ".join(v for v in vals if v)
            if fname and val:
                triples.append((gname, fname, val))
    return build_single_model_sheet(brand, brand_name, series, model, triples, url)


# ==================== 通用：取 __NEXT_DATA__ ====================
_NEXT_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def next_data(html: str):
    """提取并解析 Next.js 站的 __NEXT_DATA__ JSON。失败返回 None。"""
    m = _NEXT_RE.search(html or "")
    if not m:
        return None
    try:
        return _json.loads(m.group(1))
    except Exception:
        return None


# ==================== LG 美国（__NEXT_DATA__ allInfo）====================
# 结构：props.pageProps.productData.allInfo = [{specGroupOrder, subtitle 分组名,
#   tableData:[{term 项目, description 值, attributeOrder}]}]。一页一个型号。


def parse_lg_us_html(html: str, brand: str, brand_name: str, series: str,
                     model: str, url: str = "") -> SpecSheet:
    data = next_data(html)
    if not data:
        return SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    try:
        allinfo = data["props"]["pageProps"]["productData"].get("allInfo") or []
    except Exception:
        allinfo = []
    triples: list[tuple[str, str, str]] = []
    for grp in allinfo:
        if not isinstance(grp, dict):
            continue
        gname = str(grp.get("subtitle") or "")
        for it in grp.get("tableData") or []:
            if not isinstance(it, dict):
                continue
            term = str(it.get("term") or "")
            desc = str(it.get("description") or "")
            if term and desc:
                triples.append((gname, term, desc))
    return build_single_model_sheet(brand, brand_name, series, model, triples, url)


# ==================== Samsung 美国（__NEXT_DATA__ 概要规格）====================
# Samsung 完整规格表通过客户端 XHR 异步加载，无头浏览器不渲染；初始 HTML 仅含
# 概要级规格（modelCode / attributes / additionalSpecs / keySummary）与 JSON-LD
# ProductGroup(hasVariant 各尺寸)。故这里产出「概要 SPEC」：型号/尺寸/面板/处理器/
# VESA/重量/关键特性等，能跑通与导出；完整规格待后续用带头浏览器或专用 API 增强。


def _jsonld_blocks(html: str) -> list:
    out = []
    for b in re.findall(r'application/ld\+json[^>]*>(.*?)</script>', html or "", re.S):
        try:
            d = _json.loads(b.strip())
        except Exception:
            continue
        out.extend(d if isinstance(d, list) else [d])
    return out


def parse_samsung_us_html(html: str, brand: str, brand_name: str, series: str,
                          model: str, url: str = "") -> SpecSheet:
    from .spec_parser import merge_single_model_sheets

    data = next_data(html)
    prod = {}
    if data:
        try:
            prod = (data["props"]["pageProps"]["productData"]["products"] or [{}])[0]
        except Exception:
            prod = {}

    # JSON-LD ProductGroup：各尺寸变体 + 评分
    group = None
    for d in _jsonld_blocks(html):
        if isinstance(d, dict) and d.get("@type") == "ProductGroup":
            group = d
            break

    # 收集变体（尺寸型号）；无变体则用当前型号
    variants = []
    if group and isinstance(group.get("hasVariant"), list):
        for v in group["hasVariant"]:
            if not isinstance(v, dict):
                continue
            sku = str(v.get("sku") or v.get("mpn") or "")
            name = str(v.get("name") or "")
            msize = re.search(r"(\d{2,3})\s*(?:inch|\")", name, re.I)
            variants.append({
                "model": (sku or name).upper(),
                "name": name,
                "size": msize.group(1) if msize else "",
                "desc": str(v.get("description") or ""),
            })
    if not variants:
        variants = [{"model": model, "name": prod.get("familyMktName", ""),
                     "size": "", "desc": ""}]

    # 公共概要项（对各尺寸变体共用）
    common: list[tuple[str, str, str]] = []
    for k, v in (prod.get("additionalSpecs") or {}).items():
        if v:
            common.append(("基本仕様", str(k), str(v)))
    attrs = prod.get("attributes") or {}
    if attrs.get("Model"):
        common.append(("基本仕様", "Model Name", str(attrs["Model"])))
    if prod.get("familyMktName"):
        common.append(("基本仕様", "Product Family", str(prod["familyMktName"])))
    if prod.get("productFeatureTitle"):
        common.append(("基本仕様", "Feature Title", str(prod["productFeatureTitle"])))
    ks = prod.get("keySummary") or []
    feats = [str(x.get("desc")) for x in ks if isinstance(x, dict) and x.get("desc")]
    if not feats and prod.get("keyFeature"):
        feats = [ln for ln in str(prod["keyFeature"]).split("\n") if ln.strip()]
    for i, f in enumerate(feats, 1):
        common.append(("主要機能", f"Key Feature {i}", f))
    if group and isinstance(group.get("aggregateRating"), dict):
        ar = group["aggregateRating"]
        if ar.get("ratingValue"):
            common.append(("評価", "Average Rating", str(ar["ratingValue"])))
        if ar.get("reviewCount") or ar.get("ratingCount"):
            common.append(("評価", "Review Count",
                           str(ar.get("reviewCount") or ar.get("ratingCount"))))

    sheets = []
    for var in variants:
        mdl = var["model"] or model
        triples = list(common)
        if var["size"]:
            triples.insert(0, ("基本仕様", "Screen Size", var["size"] + '"'))
        if var["name"]:
            triples.insert(0, ("基本仕様", "Product Name", var["name"]))
        sheets.append(build_single_model_sheet(brand, brand_name, series, mdl,
                                               triples, url))
    sheets = [s for s in sheets if s.rows]
    if not sheets:
        return SpecSheet(brand=brand, brand_name=brand_name, series=series, url=url)
    return merge_single_model_sheets(sheets, brand, brand_name, series, url)


# ==================== Hisense 美国（Wix，概要规格）====================
# Hisense 美国站规格由 Wix ProductSpecification 组件从后端 API 异步渲染，
# 无头浏览器不填充，初始 HTML/warmup 无结构化规格、也无 JSON-LD。可稳定获取的
# 只有产品标题（含尺寸/系列/类型）、URL 里的型号，及营销描述里的零散规格词。
# 故产出「概要 SPEC」：型号/尺寸/系列/面板类型/分辨率/系统/刷新率/HDR/音频等，
# 足以跑通与导出；完整规格待后续用带头浏览器或 Hisense 规格 API 增强。

# 标题形如：Hisense 65" Class U8 Series MiniLED ULED 4K Google TV
_HIS_SIZE = re.compile(r'(\d{2,3})"?\s*Class', re.I)
_HIS_SERIES = re.compile(r'\b([A-Z]{1,3}\d{1,2}[A-Z]?)\s+Series', re.I)


def parse_hisense_us(title: str, page_text: str, model: str, series: str,
                     brand: str, brand_name: str, url: str = "") -> SpecSheet:
    title = title or ""
    text = page_text or ""
    triples: list[tuple[str, str, str]] = []

    triples.append(("基本仕様", "Model", model))
    msize = _HIS_SIZE.search(title)
    if msize:
        triples.append(("基本仕様", "Screen Size", msize.group(1) + '"'))
    # 面板/背光类型
    for kw in ("RGB-MiniLED", "MiniLED", "Mini-LED", "ULED", "QLED", "OLED", "UHD"):
        if kw.lower() in title.lower():
            triples.append(("画面", "Display Type", kw))
            break
    # 分辨率
    if "8k" in title.lower():
        triples.append(("画面", "Resolution", "8K"))
    elif "4k" in title.lower():
        triples.append(("画面", "Resolution", "4K UHD"))
    # 系统
    for os_kw in ("Google TV", "Fire TV", "Roku TV", "Vidaa"):
        if os_kw.lower() in title.lower():
            triples.append(("スマート", "Smart Platform", os_kw))
            break
    # 从描述提取零散规格（尽力而为）
    m = re.search(r"(\d{2,3})\s*Hz\s*Native Refresh", text, re.I)
    if m:
        triples.append(("画面", "Refresh Rate", m.group(1) + "Hz Native"))
    for hdr in ("Dolby Vision", "HDR10+", "HDR10", "HLG"):
        if hdr.lower() in text.lower():
            triples.append(("画面", "HDR", hdr))
            break
    m = re.search(r"(\d{2,3}W)[^.]*Audio", text)
    if m:
        triples.append(("音声", "Audio Power", m.group(1)))
    if "dolby atmos" in text.lower():
        triples.append(("音声", "Dolby Atmos", "Yes"))

    return build_single_model_sheet(brand, brand_name, series, model, triples, url)


# ==================== Hisense 美国 Full Specs（点击展开后的完整规格）====================
# 点击产品页「Full Specs」后，ProductSpecification 容器 innerText 是完整规格：
# 分组标题独立成行 + 其后 key\nvalue 交替。下列为实测分组标题枚举。
_HIS_GROUPS = {
    "product dimensions", "display", "type of tv", "picture quality", "audio",
    "languages", "power", "connectivity", "ports", "other features",
    "accessories", "warranty", "wall mount", "general",
    "dimensions", "video", "sound", "network", "tuner",
    "power & energy", "size & weight",
}


def parse_hisense_us_fullspecs(spec_text: str, model: str, series: str,
                               brand: str, brand_name: str,
                               url: str = "") -> SpecSheet:
    """解析 Hisense 产品页点击 Full Specs 后 ProductSpecification 容器的 innerText。

    文本形如（每项独占一行）：分组标题(命中 _HIS_GROUPS) 独立成行，其后 key\\nvalue 交替。
    """
    lines = [ln.strip() for ln in (spec_text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    triples: list[tuple[str, str, str]] = []
    group = ""
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lower() in _HIS_GROUPS:
            group = ln
            i += 1
            continue
        if i + 1 < len(lines) and lines[i + 1].lower() not in _HIS_GROUPS:
            triples.append((group, ln, lines[i + 1]))
            i += 2
        else:
            i += 1
    return build_single_model_sheet(brand, brand_name, series, model, triples, url)


# ==================== Samsung 美国 Full Specs（滚动渲染后结构化提取）====================
# Samsung 完整规格表滚动到底后由客户端渲染出结构化 DOM：
#   [class*='specgroupContainer']  分组容器
#     [class*='specGroupName']     分组名（区分）
#     [class*='subSpecsItem']      每项
#       [class*='subSpecItemName'] 项目名
#       [class*='subSpecsItemValue'] 值
# 适配器用 dom.eval_js 遍历取出 [(分组, 项目, 值), ...]，本函数据此构建 SpecSheet。

def parse_samsung_us_fullspecs(triples: list, model: str, series: str,
                               brand: str, brand_name: str,
                               url: str = "") -> SpecSheet:
    clean: list[tuple[str, str, str]] = []
    for t in triples or []:
        if not isinstance(t, (list, tuple)) or len(t) < 3:
            continue
        grp, name, val = str(t[0] or ""), str(t[1] or ""), str(t[2] or "")
        if name and val:
            clean.append((grp, name, val))
    return build_single_model_sheet(brand, brand_name, series, model, clean, url)
