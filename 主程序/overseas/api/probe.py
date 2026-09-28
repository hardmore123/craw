"""站点可访问性验证（通道验证）：检查目标站点是否能访问、是否被拦。

策略：默认用轻量 HTTP（快，秒级）；`use_browser=True` 时用 Playwright（更接近真实
抓取环境，能识别 JS 渲染的软拦截页，但慢）。返回状态码、耗时、是否被拦与原因。

用于前端"验证是否可以访问要抓取的网站"，抓取前先探一下通道。
"""
from __future__ import annotations

import time
from typing import Any

from .. import config
from ..infra import BlockDetector, RateLimiter


def probe_url(url: str, use_browser: bool = False, timeout: float = 20.0) -> dict[str, Any]:
    """探测单个 URL。返回 {url, ok, status, blocked, block_reason, elapsed_ms, error, mode}。"""
    if not url or not url.startswith(("http://", "https://")):
        return {"url": url, "ok": False, "status": 0, "blocked": False,
                "block_reason": "", "elapsed_ms": 0, "error": "非法 URL", "mode": "-"}
    detector = BlockDetector()
    t0 = time.time()
    if use_browser:
        return _probe_browser(url, detector, t0)
    return _probe_http(url, detector, t0, timeout)


def _result(url, ok, status, blocked, reason, t0, error, mode) -> dict[str, Any]:
    return {
        "url": url, "ok": bool(ok), "status": int(status or 0),
        "blocked": bool(blocked), "block_reason": reason or "",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "error": error or "", "mode": mode,
    }


def _probe_http(url, detector, t0, timeout) -> dict[str, Any]:
    import urllib.error
    import urllib.request
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    }
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            text = resp.read(8192).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            text = e.read(8192).decode("utf-8", "replace")
        except Exception:
            text = ""
    except Exception as e:
        return _result(url, False, 0, False, "", t0,
                       f"{type(e).__name__}: {str(e)[:120]}", "http")
    blocked, reason = detector.check(status, text)
    ok = (200 <= status < 400) and not blocked
    return _result(url, ok, status, blocked, reason, t0, "", "http")


def _probe_browser(url, detector, t0) -> dict[str, Any]:
    from ..fetchers import BrowserFetcher
    from ..infra import DiskCache
    fetcher = BrowserFetcher(rate_limiter=RateLimiter(min_interval=0.0),
                             detector=detector, cache=DiskCache(enabled=False))
    try:
        with fetcher.page(url) as (res, dom):
            ok = res.ok and not res.blocked
            return _result(url, ok, res.status, res.blocked, res.block_reason,
                           t0, res.error, "browser")
    except Exception as e:
        return _result(url, False, 0, False, "", t0,
                       f"{type(e).__name__}: {str(e)[:120]}", "browser")
    finally:
        fetcher.close()
