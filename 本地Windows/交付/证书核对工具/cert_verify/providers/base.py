"""Provider 基类 + HTML 工具函数。"""
from __future__ import annotations

import html
import re

from ..models import CertRecord


class BaseProvider:
    """一个认证机构对应一个 Provider。"""

    name = "base"

    # 该机构证书号的识别特征，用于自动路由（顺序匹配）
    number_patterns: tuple[str, ...] = ()

    def matches(self, cert_no: str) -> bool:
        s = (cert_no or "").strip().upper()
        return any(re.search(p, s) for p in self.number_patterns)

    def query(self, cert_no: str) -> CertRecord:  # pragma: no cover - 接口
        raise NotImplementedError


# ---------------- HTML 工具 ----------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u00a0]+")


def text_of(fragment: str, keep_lines: bool = False) -> str:
    """去标签取纯文本。keep_lines=True 时把块级标签换成换行，便于保留多行字段。"""
    s = fragment or ""
    if keep_lines:
        s = re.sub(r"(?i)<br\s*/?>", "\n", s)
        s = re.sub(r"(?i)</(p|div|li|tr|h\d)>", "\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines) if keep_lines else " ".join(lines).strip()


def first_group(pattern: str, source: str, flags: int = re.S | re.I) -> str:
    m = re.search(pattern, source, flags)
    return m.group(1) if m else ""
