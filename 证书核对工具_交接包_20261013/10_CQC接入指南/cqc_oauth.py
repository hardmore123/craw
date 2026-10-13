"""CQC 数字开放平台 OAuth2 授权码接入（后端一次授权 + refresh_token 自动续期）。

流程与官方示例 client-demo-origin 一致：
  1. 浏览器访问 /api/cqc/authorize → 302 跳到 CQC 授权页人工登录
  2. CQC 回调 /api/cqc/callback?code=...&state=... → 用 code 换 token
  3. 持久化 refresh_token；核对任务运行时用它自动换 access_token

设计约束：
- 只依赖标准库（urllib），与 platform_report.py 风格一致。
- CQC 是境内平台，默认直连，不走 VDE/UL 的境外代理；可用 CQC_PROXY 覆盖。
- access_token / refresh_token 落在 .cqc_profile/token.json，绝不进 Git。
- 授权码模式的客户端认证用 HTTP Basic（client_secret_basic）。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ---------------- 配置（环境变量 / .env）----------------
ENABLED = os.getenv("CQC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
CLIENT_ID = os.getenv("CQC_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("CQC_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.getenv("CQC_REDIRECT_URI", "").strip()
ISSUER = os.getenv("CQC_ISSUER", "https://open.cqccms.com.cn/open-auth").rstrip("/")
GATEWAY = os.getenv("CQC_GATEWAY", "https://open.cqccms.com.cn/open-gateway").rstrip("/")
PROXY = os.getenv("CQC_PROXY", "").strip()      # 默认空 = 直连
try:
    TIMEOUT = max(1.0, float(os.getenv("CQC_TIMEOUT", "30")))
except ValueError:
    TIMEOUT = 30.0

AUTHORIZE_ENDPOINT = f"{ISSUER}/oauth2/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/oauth2/token"

_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cqc_profile")
_TOKEN_PATH = os.path.join(_PROFILE_DIR, "token.json")

_lock = threading.Lock()
# 授权发起时生成的 state（防 CSRF），回调时校验；内存态即可（单进程）。
_pending_states: dict[str, float] = {}
_STATE_TTL = 600.0        # 10 分钟未回调则作废


# ---------------- 配置校验 ----------------
def is_configured() -> bool:
    """client_id / client_secret / redirect_uri 三项齐全才算配置完整。"""
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def is_authorized() -> bool:
    """本地已存有 refresh_token 视为已授权。"""
    tok = _load_tokens()
    return bool(tok.get("refresh_token"))


def status() -> dict:
    tok = _load_tokens()
    expiry = tok.get("expiry")
    return {
        "enabled": ENABLED,
        "configured": is_configured(),
        "authorized": bool(tok.get("refresh_token")),
        "has_access_token": bool(tok.get("access_token")),
        "access_expiry": expiry,
        "client_id_set": bool(CLIENT_ID),
        "redirect_uri": REDIRECT_URI,
    }


# ---------------- 令牌持久化 ----------------
def _load_tokens() -> dict:
    try:
        with open(_TOKEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_tokens(data: dict) -> None:
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    tmp = _TOKEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _TOKEN_PATH)
    try:                              # 尽力收紧权限（Windows 上无效则忽略）
        os.chmod(_TOKEN_PATH, 0o600)
    except OSError:
        pass


def _store_token_response(resp: dict) -> None:
    """把 token 端点返回写入本地，保留旧 refresh_token（某些刷新不返回新的）。"""
    with _lock:
        cur = _load_tokens()
        access = resp.get("access_token")
        refresh = resp.get("refresh_token") or cur.get("refresh_token")
        expires_in = resp.get("expires_in")
        try:
            expiry = int(time.time()) + int(expires_in) if expires_in else 0
        except (TypeError, ValueError):
            expiry = 0
        cur.update({
            "access_token": access,
            "refresh_token": refresh,
            "token_type": resp.get("token_type") or cur.get("token_type") or "Bearer",
            "scope": resp.get("scope", cur.get("scope", "")),
            "expiry": expiry,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        _save_tokens(cur)


# ---------------- 授权码流程 ----------------
def new_state() -> str:
    state = secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        # 顺手清理过期 state
        for k in [k for k, t in _pending_states.items() if now - t > _STATE_TTL]:
            _pending_states.pop(k, None)
        _pending_states[state] = now
    return state


def check_state(state: str) -> bool:
    with _lock:
        ts = _pending_states.pop(state, None)
    return ts is not None and (time.time() - ts) <= _STATE_TTL


def build_authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(params, safe="")


def _opener() -> urllib.request.OpenerDirector:
    # 默认直连（境内平台）；CQC_PROXY 显式设置时才走代理。
    handlers = [urllib.request.ProxyHandler({} if not PROXY else {"http": PROXY, "https": PROXY})]
    return urllib.request.build_opener(*handlers)


def _token_request(form: dict) -> dict:
    body = urllib.parse.urlencode(form).encode("utf-8")
    basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + basic,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"CQC token 请求失败 HTTP {e.code}：{detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"CQC token 请求网络错误：{e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"CQC token 响应非 JSON：{raw[:300]}") from e
    if "access_token" not in data:
        raise RuntimeError(f"CQC token 响应缺少 access_token：{raw[:300]}")
    return data


def exchange_code(code: str) -> None:
    """用授权码换 token 并持久化。"""
    if not is_configured():
        raise RuntimeError("CQC 未配置：需要 CQC_CLIENT_ID / CQC_CLIENT_SECRET / CQC_REDIRECT_URI")
    data = _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })
    _store_token_response(data)
    log.info("CQC 授权成功，已保存 refresh_token")


def refresh() -> None:
    """用 refresh_token 换新的 access_token。"""
    tok = _load_tokens()
    rt = tok.get("refresh_token")
    if not rt:
        raise RuntimeError("尚未授权：本地没有 refresh_token，请先完成 /api/cqc/authorize")
    data = _token_request({"grant_type": "refresh_token", "refresh_token": rt})
    _store_token_response(data)


def get_access_token(margin: int = 60) -> str:
    """返回有效 access_token；快过期则自动用 refresh_token 续期。"""
    tok = _load_tokens()
    access = tok.get("access_token")
    expiry = tok.get("expiry") or 0
    if access and expiry and time.time() < (expiry - margin):
        return access
    refresh()
    return _load_tokens().get("access_token", "")


def clear() -> None:
    """清除本地令牌（撤销/重置用）。"""
    with _lock:
        try:
            os.remove(_TOKEN_PATH)
        except FileNotFoundError:
            pass


# ---------------- 网关调用（通用，供后续 provider 使用）----------------
def call_gateway(method: str, version: str, extra_params: dict | None = None) -> str:
    """按官方示例形式调用 open-gateway，自动带 Bearer token，返回响应文本。

    URL 形如：{gateway}?client_id=..&method=..&version=..&timestamp=..&<extra>
    具体 method/version/参数以平台「我的接口」页 cmsent 接口定义为准（暂未接入）。
    """
    token = get_access_token()
    if not token:
        raise RuntimeError("无有效 access_token")
    params = {
        "client_id": CLIENT_ID,
        "method": method,
        "version": version,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra_params:
        params.update(extra_params)
    url = GATEWAY + "?" + urllib.parse.urlencode(params, safe="")
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"CQC 网关请求失败 HTTP {e.code}：{detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"CQC 网关请求网络错误：{e}") from e
