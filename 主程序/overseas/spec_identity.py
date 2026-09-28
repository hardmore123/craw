"""SPEC 内部身份辅助函数。"""
from __future__ import annotations

import re


_FAMILY_SUFFIX = re.compile(r"\s+\[family_id=(?P<family_id>[^\]]+)\]\s*$", re.I)


def family_series_key(series: str, family_id: str) -> str:
    """为存在 family 身份的系列生成稳定内部键。

    注意：``series`` 只做两端 strip，不折叠内部空白。写入 spec_series /
    spec_series_status 的键就是适配器传入的官网原文（可能含连续空格，如
    Samsung 的 "SIZE INCH THE SERIF  LS01D"）。此处若把内部空白归一化，
    生成的键会与已入库键不一致，导致续跑/导出误判为未成功系列。
    """
    display = str(series or "").strip()
    identity = str(family_id or "").strip()
    if not display or not identity:
        return display
    return f"{display} [family_id={identity}]"


def display_series_name(series: str) -> str:
    """去除内部 family 后缀，返回官网展示用系列名。

    只去掉 [family_id=...] 内部后缀并 strip 两端，保留系列名原有内部空白，
    与官网展示口径和入库键保持一致。
    """
    text = str(series or "")
    return _FAMILY_SUFFIX.sub("", text).strip()
