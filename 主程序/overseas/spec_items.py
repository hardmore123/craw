"""SPEC 项目主行名规范化。

这里处理的是已经确认不会改变规格语义的页面异写/脏数据，结果会写入
SpecRow.item_ja，供同系列合并、数据库快照和导出共同使用。原始页面仍可
从采集日志/站点页面追溯；不会把 mm 与 kg、尺寸与重量、不同基准年合并。

重点规则：
- 统一空白、斜杠和括号写法；移除项目名上的页面脚注标记；
- 梱包カートン 的重复字、空格、Kg/kg 统一，但 mm 和 kg 保留；
- 梱包質量 明确为 kg，统一到梱包質量(kg)；
- 梱包寸法 在值中明确单位时把单位补到主名，避免 cm/mm 混在同一行；
- REGZA 梱包箱寸法的方向描述异写统一；
- Sony 梱包尺寸/重量主名末尾的页面编号去掉。
"""
from __future__ import annotations

import re


_FOOTNOTE_RE = re.compile(r"[※＊*]\s*\d{1,2}")
_UNIT_RE = re.compile(r"\(\s*(mm|cm|kg)\s*\)", re.IGNORECASE)
_VALUE_UNIT_RE = re.compile(r"(?<![A-Za-z])(mm|cm|kg)(?![A-Za-z])", re.IGNORECASE)


def _unit_from_item(text: str) -> str:
    match = _UNIT_RE.search(text)
    return match.group(1).lower() if match else ""


def _unit_from_value(value: str) -> str:
    match = _VALUE_UNIT_RE.search(value or "")
    return match.group(1).lower() if match else ""


def _normalize_surface(text: str) -> str:
    """只统一书写表面，不做语义推断。"""
    text = (text or "").replace("\u200e", "").replace("\u200f", "")
    text = text.replace("\u3000", " ").replace("／", "/")
    text = text.replace("（", "(").replace("）", ")")
    text = _FOOTNOTE_RE.sub("", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # 仅移除标点周围的版式空格，不删除英文词内部空格（如 Web Browser）。
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s+\(", "(", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text.strip(" /")


def canonical_item_ja(item_ja: str, sample_value: str = "") -> str:
    """返回可入库的 SPEC 主行名。

    ``sample_value`` 用于少数官网把单位写在值而不是项目名的情况。只对
    明确属于包装尺寸/重量的项目读取该单位，普通规格行不会被值内容影响。
    """
    text = _normalize_surface(item_ja)
    if not text:
        return ""

    # 已确认的官网脏数据/同义主干。
    text = text.replace("梱梱包カートン", "梱包カートン")
    text = text.replace("スタンドなし", "スタンド除く")

    # Sony：页面脚注/版本编号附着在主名末尾，不是规格语义。
    text = re.sub(
        r"^(梱包サイズ\(幅×高さ×奥行\)：cm)\d{1,2}$",
        r"\1", text)
    text = re.sub(
        r"^(梱包サイズ\(質量\)：kg)\d{1,2}$",
        r"\1", text)

    # 所有单位括号统一成小写；Kg/KG 只影响单位写法，不改变项目含义。
    text = _UNIT_RE.sub(lambda m: f"({m.group(1).lower()})", text)

    item_unit = _unit_from_item(text)
    value_unit = _unit_from_value(sample_value)

    # TCL：包装箱尺寸/重量是同一项目的固定主名，单位必须保留。
    if text.startswith("梱包カートン"):
        unit = item_unit or value_unit
        return f"梱包カートン({unit})" if unit else "梱包カートン"

    # 海信/松下：包装质量的单位有时只出现在值里，统一主名避免“包装质量”
    # 与“包装质量（kg）”分裂；质量与尺寸本来就是不同项目。
    if text == "梱包質量" or text == "梱包質量(kg)":
        return "梱包質量(kg)"

    # REGZA：有的系列带“幅×高×深”，有的系列不带；方向描述是同一主项。
    if text == "梱包箱寸法" or text == "梱包箱寸法 幅×高さ×奥行":
        return "梱包箱寸法 幅×高さ×奥行"

    # 海信/松下：方向描述和单位的组合统一到词典已有的规范写法。
    if text.startswith("梱包寸法"):
        text = text.replace("梱包寸法/", "梱包寸法")
        has_direction = "幅×高さ×奥行" in text
        unit = item_unit or value_unit
        if has_direction:
            base = "梱包寸法幅×高さ×奥行"
            return f"{base}({unit})" if unit else base
        if text == "梱包寸法" and unit:
            return f"梱包寸法({unit})"

    return text
