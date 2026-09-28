"""零售线合规白名单准入（P1-3）。

零售站 robots/ToS 比品牌官网更严格，不能给个 URL 就抓。本模块提供三层：
    1) 白名单：只有声明允许抓取的零售站域名才能进抓取流程
    2) robots 检查：抓取前读 robots.txt，被禁则告警（OVERSEAS_ROBOTS_HARD_DENY=1 可硬拒）
    3) 校验入口：`ensure_allowed(code, url)` 供 `crawl_model_on_site` / CLI 统一调用

白名单来源（合并）：
    - 内置默认（本文件 _DEFAULT_ALLOWED_HOSTS，覆盖已接入站点）
    - OVERSEAS_RETAIL_ALLOWLIST 指向的 JSON 文件（list[str] 或 {"hosts": [...]}）
    - OVERSEAS_RETAIL_ALLOWED_HOSTS 环境变量（逗号分隔）

使用方式：
    from overseas.retail_compliance import ensure_allowed
    ensure_allowed("https://www.liverpool.com.mx/tienda?s=x")   # 不过则抛 ComplianceError
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

# 内置默认白名单（已接入零售站的注册域名）。新增站点先在这里登记或走外部配置。
_DEFAULT_ALLOWED_HOSTS: set[str] = {
    # 墨西哥
    "liverpool.com.mx",
    "coppel.com",
    "elpalaciodehierro.com",
    "sams.com.mx",
    "amazon.com.mx",
    "walmart.com.mx",
    # 加拿大
    "amazon.ca",
    "bestbuy.ca",
    "walmart.ca",
    "costco.ca",
    "thebrick.com",
    "leons.ca",
    "visions.ca",
    "canadiantire.ca",
    # 美国
    "amazon.com",
    "bestbuy.com",
    "walmart.com",
    "costco.com",
    # 日本 kakaku（价格/网评聚合）
    "kakaku.com",
}

# 每个 host 的 Disallow 路径前缀缓存（避免每请求读一次 robots.txt）
_ROBOTS_CACHE: dict[str, list[str]] = {}


class ComplianceError(RuntimeError):
    """白名单/robots 校验不过。reason: denied | robots_disallowed。"""

    def __init__(self, reason: str = "denied", message: str = ""):
        super().__init__(message)
        self.reason = reason
        self.message = message


def host_of(url: str) -> str:
    """从 URL 提取纯 host（去协议/端口/userinfo）。"""
    m = re.match(r"https?://([^/?#]+)", (url or "").strip(), re.I)
    if not m:
        return ""
    host = m.group(1).lower().split("@")[-1]
    host = host.split(":")[0]
    return host


def _clean_host(h: str) -> str:
    h = (h or "").strip().lower()
    h = h.replace("https://", "").replace("http://", "").replace("*.", "")
    return h


def load_allowlist() -> set[str]:
    """合并内置默认 + 配置文件 + 环境变量，返回允许 host 集合。"""
    hosts = {_clean_host(h) for h in _DEFAULT_ALLOWED_HOSTS}

    # 配置文件（可选）
    env_cfg = os.environ.get("OVERSEAS_RETAIL_ALLOWLIST") or ""
    data_dir = str(getattr(config, "DATA_DIR", "data"))
    for path in (env_cfg, str(Path(data_dir) / "retail_allowlist.json")):
        if not path:
            continue
        p = Path(path)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            hosts.update(_clean_host(h) for h in data if isinstance(h, str))
        elif isinstance(data, dict) and isinstance(data.get("hosts"), list):
            hosts.update(_clean_host(h) for h in data["hosts"] if isinstance(h, str))

    env_hosts = os.environ.get("OVERSEAS_RETAIL_ALLOWED_HOSTS") or ""
    hosts.update(_clean_host(h) for h in env_hosts.split(",") if h.strip())
    return {h for h in hosts if re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}$", h)}


ALLOWED_HOSTS = load_allowlist()


def ensure_allowed(url: str, *, host: str = "", check_robots: bool = True) -> str:
    """抓取前合规校验。返回 host；不合规则抛 ComplianceError。

    check_robots=True 时读该 host 的 robots.txt（进程内缓存），被明确禁用的路径
    按 OVERSEAS_ROBOTS_HARD_DENY 决定硬拒（默认 False 只提示）。
    """
    host = host or host_of(url)
    if not host:
        raise ComplianceError("denied", f"无法解析 URL host（URL={url!r}）")
    # 兼容 www. 前缀与裸域名：www.example.com 与 example.com 视为同一站点
    candidates = {host}
    if host.startswith("www."):
        candidates.add(host[4:])
    elif f"www.{host}" in ALLOWED_HOSTS:
        candidates.add(f"www.{host}")
    if not (candidates & ALLOWED_HOSTS):
        raise ComplianceError(
            "denied",
            f"站点不在零售白名单: {host}\n"
            f"如确认合规，把 {host} 加到 OVERSEAS_RETAIL_ALLOWED_HOSTS 或"
            f" data/retail_allowlist.json 后再运行。")
    if check_robots:
        path = urllib.parse.urlsplit(url).path or "/"
        if robots_disallows(host, path):
            hard = (os.environ.get("OVERSEAS_ROBOTS_HARD_DENY") or "").strip().lower() \
                in {"1", "true", "yes", "on"}
            if hard:
                raise ComplianceError(
                    "robots_disallowed",
                    f"robots.txt 明确禁抓 {host}{path}（硬拒绝模式 OVERSEAS_ROBOTS_HARD_DENY=1）")
            print(f"[compliance] ⚠ robots.txt 禁抓 {host}{path}（软拒绝，继续）")
    return host


def fetch_robots(host: str) -> list[str]:
    """读 host 的 robots.txt，返回 Disallow 路径前缀列表；失败返回 [] 不阻塞抓取。"""
    if host in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[host]
    paths: list[str] = []
    url = f"https://{host}/robots.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", "replace")
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            if line.lower().startswith("disallow:"):
                val = line.split(":", 1)[1].strip()
                if val:
                    paths.append(val)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        paths = []
    _ROBOTS_CACHE[host] = paths
    return paths


def robots_disallows(host: str, path: str) -> bool:
    """path 是否被该 host 的 robots.txt 明确禁抓。空 allow 视为不限制。"""
    disallows = fetch_robots(host)
    if not disallows:
        return False
    for prefix in disallows:
        if prefix == "/":
            return True      # 全站禁止
        if prefix and path.startswith(prefix):
            return True
    return False


def reset_cache() -> None:
    _ROBOTS_CACHE.clear()


# 兼容既有命名（旧脚本用 RiskError 表示合规风险）
RiskError = ComplianceError


__all__ = [
    "ALLOWED_HOSTS", "ComplianceError", "RiskError", "ensure_allowed",
    "fetch_robots", "host_of", "load_allowlist", "reset_cache", "robots_disallows",
]