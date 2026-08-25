"""Provider 注册与路由。"""
from __future__ import annotations

from ..http_client import HttpClient
from ..models import UNSUPPORTED, CertRecord
from .base import BaseProvider
from .tuv_rheinland import TuvRheinlandProvider
from .ul import UlProvider
from .vde import VdeProvider

__all__ = [
    "BaseProvider", "TuvRheinlandProvider", "UlProvider", "VdeProvider",
    "ProviderRouter",
]


class ProviderRouter:
    """按 表格里的"认证机构"列 或 证书号形态 选 Provider。"""

    def __init__(self, client: HttpClient | None = None, headless: bool = True,
                 proxy: str | None = None, interval: float | None = None,
                 jitter: float | None = None):
        if client is None:
            kw = {}
            if interval is not None:
                kw["min_interval"] = max(0.0, interval)
            if jitter is not None:
                kw["jitter"] = max(0.0, jitter)
            client = HttpClient(proxy=proxy, **kw)
        # UL 是真浏览器：iq.ulprospector.com 直连会被网关 SSL 拦截，必须走代理。
        # 实测同一个 VPN 代理(如 127.0.0.1:7877)既能通 VDE 也能通 UL，所以让 UL 和
        # VDE 用同一个 proxy；UL 内部再按 显式proxy > UL_PROXY > 系统代理 兜底。
        ul = UlProvider(headless=True, proxy=proxy, interval=interval, jitter=jitter)
        ul._reuse = True          # 批量核对时复用同一个浏览器会话，别每行重启 Edge
        # 顺序影响「无提示时按证书号形态猜」的优先级：VDE 的 8 位数字与 TÜV 的
        # ^\d{8}$ 会冲突，本项目以 VDE(CB) 为主，故 VDE 排在 TÜV 前面。
        self.providers: dict[str, BaseProvider] = {
            "VDE": VdeProvider(headless=headless, proxy=proxy, client=client),
            "UL": ul,
            "TUV-Rheinland": TuvRheinlandProvider(client),
        }
    # 证书号显式前缀 -> provider key（None 表示暂不搜索，如 ENEC）
    _PREFIX_RE = __import__("re").compile(
        r"^\s*(UL|VDE|T[ÜU]V|ENEC)\b", __import__("re").I)

    @classmethod
    def _cert_prefix(cls, cert_no: str) -> str:
        m = cls._PREFIX_RE.match(cert_no or "")
        if not m:
            return ""
        p = m.group(1).upper().replace("Ü", "U")
        return p           # UL / VDE / TUV / ENEC

    def resolve(self, cert_no: str, agency_hint: str = "") -> BaseProvider | None:
        """决定用哪个网站。优先级：

          1. 证书号显式前缀（最可信）：'TUV …'→TÜV，'VDE …'→VDE，'UL …'→UL；
             'ENEC …' 暂不搜索（返回 None）。
          2. 无显式前缀时按「认证类别」列：含 UL(含 CB/UL)→UL；纯 CB→VDE；
             含 VDE→VDE；含 TUV/莱茵→TÜV。
          3. 都判不出再按证书号形态猜。
        """
        pref = self._cert_prefix(cert_no)
        if pref == "ENEC":
            return None                        # ENEC 暂不支持，交人工
        if pref == "UL":
            return self.providers["UL"]
        if pref == "VDE":
            return self.providers["VDE"]
        if pref == "TUV":
            return self.providers["TUV-Rheinland"]

        h = (agency_hint or "").strip().lower()
        if h:
            if "ul" in h:
                return self.providers["UL"]
            if "vde" in h:
                return self.providers["VDE"]
            if any(k in h for k in ("tüv", "tuv", "rheinland", "莱茵")):
                return self.providers["TUV-Rheinland"]
            if "cb" in h:                      # 纯 CB → VDE（VDE 是 CB 证书的核对源）
                return self.providers["VDE"]
        for p in self.providers.values():      # 退化到按证书号形态猜
            if p.matches(cert_no):
                return p
        return None

    def query(self, cert_no: str, agency_hint: str = "") -> CertRecord:
        # 不含数字的“证书号”不是有效证书（如 CB/UL 里的 "Tested with Equipment"），跳过不查
        if not any(ch.isdigit() for ch in (cert_no or "")):
            return CertRecord(query_no=cert_no, source="?", status=UNSUPPORTED,
                              message="非有效证书号（如随设备测试 Tested with Equipment），已跳过")
        p = self.resolve(cert_no, agency_hint)
        if p is None:
            if self._cert_prefix(cert_no) == "ENEC":
                return CertRecord(query_no=cert_no, source="ENEC", status=UNSUPPORTED,
                                  message="ENEC 证书暂不自动核对，请人工核对")
            return CertRecord(query_no=cert_no, source="?", status=UNSUPPORTED,
                              message=f"无法判断认证机构（证书号={cert_no!r}, 提示={agency_hint!r}）")
        return p.query(cert_no)

    def close(self) -> None:
        """释放各 Provider 持有的资源（如 UL 的浏览器会话）。批量结束时调用。"""
        for p in self.providers.values():
            close = getattr(p, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
