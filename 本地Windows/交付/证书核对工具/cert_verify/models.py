"""统一的证书数据模型。

各认证机构（VDE / UL / TUV）网站字段名不同，全部映射到 CertRecord，
后续比对只面向 CertRecord，不关心来源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# 查询结果状态
FOUND = "found"            # 查到证书
NOT_FOUND = "not_found"    # 网站无此证书号
ERROR = "error"            # 网络/解析失败，需重试
BLOCKED = "blocked"        # 被防火墙/反爬拦截
UNSUPPORTED = "unsupported"  # 该机构暂未接入


@dataclass
class CertRecord:
    """一条证书的网站查询结果。"""

    # 查询输入
    query_no: str = ""            # 表格 I 列原始证书号
    source: str = ""              # VDE / UL / TUV-Rheinland / TUV-SUD
    status: str = ERROR           # 见上面常量
    message: str = ""             # 失败原因 / 备注

    # 五个待比对字段
    manufacturer: str = ""        # 制造商（证书持有人 / Applicant）
    factory: str = ""             # 生产厂（Manufacturing plant / Factory）
    model: str = ""               # 型号（Model designation / Type）
    standard: str = ""            # 标准（Fulfilled standards）
    cert_status: str = ""         # 证书状态（有效 / 失效 / 撤销）

    # 辅助信息
    cert_no_on_site: str = ""     # 网站上显示的完整证书号
    issue_date: str = ""
    expiry_date: str = ""
    cert_type: str = ""
    source_url: str = ""
    raw_fields: dict = field(default_factory=dict)   # 原始 label -> value，便于排查

    @property
    def ok(self) -> bool:
        return self.status == FOUND

    def get(self, key: str) -> Optional[str]:
        return getattr(self, key, None)
