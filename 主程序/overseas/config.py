"""全局配置：全部通过环境变量覆盖，默认值可直接本地跑通。

命名统一用 OVERSEAS_ 前缀，避免和 cert_verify 的 CERT_* 混淆。
生产环境用 systemd 的 EnvironmentFile 注入（见 deploy/overseas.service）。
"""
from __future__ import annotations

import os

# ---------------- 路径 ----------------
# BASE_DIR 指向「海外」目录（overseas 包的父目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("OVERSEAS_DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("OVERSEAS_DB_PATH") or os.path.join(DATA_DIR, "overseas.db")
CACHE_DIR = os.environ.get("OVERSEAS_CACHE_DIR") or os.path.join(DATA_DIR, "cache")
RAW_DIR = os.environ.get("OVERSEAS_RAW_DIR") or os.path.join(DATA_DIR, "raw")
LOG_DIR = os.environ.get("OVERSEAS_LOG_DIR") or os.path.join(DATA_DIR, "logs")
# 登录态（Playwright storage_state）落盘目录。内含 cookie，属敏感凭据，勿入版本库。
SESSION_DIR = os.environ.get("OVERSEAS_SESSION_DIR") or os.path.join(DATA_DIR, "session")
# 持久化浏览器 profile 目录（真实用户目录，反爬更信任，登录态更稳）
PROFILE_DIR = os.environ.get("OVERSEAS_PROFILE_DIR") or os.path.join(DATA_DIR, "browser_profile")


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


# ---------------- 抓取节流（礼貌抓取，按域名生效）----------------
# 默认值偏保守：强防护站点（Amazon 等）宁慢不封。
MIN_INTERVAL = _num("OVERSEAS_MIN_INTERVAL", 6.0)      # 同域名最小间隔（秒）
JITTER = _num("OVERSEAS_JITTER", 3.0)                  # 额外随机抖动 0~JITTER
# 新变量优先；保留 OVERSEAS_MAX_RETRIES 作为旧部署配置的兼容回退。
MAX_RETRIES_PER_REQUEST = max(
    0,
    _int("OVERSEAS_MAX_RETRIES_PER_REQUEST", _int("OVERSEAS_MAX_RETRIES", 2)),
)
MAX_RETRIES = MAX_RETRIES_PER_REQUEST
MAX_CONSECUTIVE_MODEL_FAILURES = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_MODEL_FAILURES", 3)
)
MAX_CONSECUTIVE_SERIES_FAILURES = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_SERIES_FAILURES", 5)
)
# ---- 零售线熔断阈值（防止 WAF 硬封时空转） ----
# 搜索页连续被拦 N 次后跳过整站（不再尝试 PDP）
MAX_CONSECUTIVE_SEARCH_BLOCKED = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_SEARCH_BLOCKED", 3)
)
# PDP 详情页连续被拦 N 次后跳过剩余产品
MAX_CONSECUTIVE_PDP_BLOCKED = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_PDP_BLOCKED", 5)
)
# PDP 连续无价格（产品页可达但价格提取空白）N 次后跳过剩余
MAX_CONSECUTIVE_NO_PRICE = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_NO_PRICE", 10)
)
# 评价页连续被拦 N 次后跳过剩余产品
MAX_CONSECUTIVE_REVIEW_BLOCKED = max(
    1, _int("OVERSEAS_MAX_CONSECUTIVE_REVIEW_BLOCKED", 3)
)
MAX_REVIEW_PAGES = max(1, _int("OVERSEAS_MAX_REVIEW_PAGES", 20))
MAX_REVIEW_THREADS = max(1, _int("OVERSEAS_MAX_REVIEW_THREADS", 60))

# ---------------- HTTP ----------------
HTTP_TIMEOUT = _num("OVERSEAS_HTTP_TIMEOUT", 30.0)
USER_AGENT = os.environ.get("OVERSEAS_USER_AGENT") or (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ---------------- 浏览器（Playwright）----------------
# 服务器上访问不了 Playwright CDN，用离线 Chrome：
#   OVERSEAS_BROWSER_EXECUTABLE=/path/to/chrome-linux64/chrome
BROWSER_EXECUTABLE = (os.environ.get("OVERSEAS_BROWSER_EXECUTABLE") or "").strip()
# 未指定离线路径时按顺序尝试系统通道（Windows 本机通常有 msedge）
BROWSER_CHANNELS = [
    c.strip() for c in
    (os.environ.get("OVERSEAS_BROWSER_CHANNELS") or "msedge,chrome,chromium").split(",")
    if c.strip()
]
HEADLESS = _flag("OVERSEAS_HEADLESS", True)
NAV_TIMEOUT_MS = int(_num("OVERSEAS_NAV_TIMEOUT", 30.0) * 1000)
# 页面加载后额外等 networkidle 的上限（毫秒），让 JS 列表/表格渲染稳定
SETTLE_MS = int(_num("OVERSEAS_SETTLE", 4.0) * 1000)
# 滚动到底触发懒加载列表的次数与每次等待（毫秒）
SCROLL_PASSES = _int("OVERSEAS_SCROLL_PASSES", 3)
SCROLL_WAIT_MS = int(_num("OVERSEAS_SCROLL_WAIT", 0.8) * 1000)
LOCALE = os.environ.get("OVERSEAS_LOCALE") or "en-CA"

# ---------------- 任务硬超时 ----------------
# 这是“每个品牌/任务进程”的总上限，不等同于单页导航 timeout。
SPEC_TIMEOUT_SECONDS = max(1.0, _num("OVERSEAS_SPEC_TIMEOUT_SECONDS", 1800.0))
REVIEW_TIMEOUT_SECONDS = max(1.0, _num("OVERSEAS_REVIEW_TIMEOUT_SECONDS", 1800.0))
JOB_TERMINATE_GRACE_SECONDS = max(
    1.0, _num("OVERSEAS_JOB_TERMINATE_GRACE_SECONDS", 10.0)
)

# Linux 无头容器/服务器必须的启动参数
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

# ---------------- 代理 ----------------
# 海外站点通常需要出海线路；留空则直连。
PROXY = (os.environ.get("OVERSEAS_PROXY") or "").strip()
PROXY_PROBE = _flag("OVERSEAS_PROXY_PROBE", True)      # 是否对代理做 TCP 探活

# ---------------- 缓存 / 存档 ----------------
CACHE_ENABLED = _flag("OVERSEAS_CACHE_ENABLED", True)
CACHE_TTL = _num("OVERSEAS_CACHE_TTL", 86400.0)        # 缓存有效期（秒）
SAVE_RAW_HTML = _flag("OVERSEAS_SAVE_RAW", False)      # 是否存档原始 HTML

# ---------------- 数据库 ----------------
# 目前实现 SQLite；DB_URL 预留给后续迁移 PostgreSQL。
DB_URL = (os.environ.get("OVERSEAS_DB_URL") or "").strip()

# ---------------- 在线翻译接口（词典未命中时的可选回退）----------------
# 设计原则：离线词典优先，接口仅作兜底。默认关闭，不影响现有离线行为。
# 开启后，dict.translate() 词典未命中的项会调用接口翻译，结果缓存到
# TRANSLATE_CACHE_PATH（人工审核后可合入主词典 spec_ja_zh.json）。
TRANSLATE_ENABLED = _flag("OVERSEAS_TRANSLATE_ENABLED", False)
# 提供商：deepl | google | custom
TRANSLATE_PROVIDER = (os.environ.get("OVERSEAS_TRANSLATE_PROVIDER") or "deepl").strip().lower()
TRANSLATE_API_KEY = (os.environ.get("OVERSEAS_TRANSLATE_API_KEY") or "").strip()
# custom 提供商：POST 到该 URL，body={"text","source","target"}，取响应 JSON 的
# TRANSLATE_CUSTOM_RESULT_PATH（点分路径，如 "data.translated"）。
TRANSLATE_CUSTOM_URL = (os.environ.get("OVERSEAS_TRANSLATE_CUSTOM_URL") or "").strip()
TRANSLATE_CUSTOM_RESULT_PATH = (
    os.environ.get("OVERSEAS_TRANSLATE_CUSTOM_RESULT_PATH") or "translatedText").strip()
TRANSLATE_SOURCE = (os.environ.get("OVERSEAS_TRANSLATE_SOURCE") or "JA").strip()
TRANSLATE_TARGET = (os.environ.get("OVERSEAS_TRANSLATE_TARGET") or "ZH").strip()
TRANSLATE_TIMEOUT = _num("OVERSEAS_TRANSLATE_TIMEOUT", 10.0)
TRANSLATE_CACHE_PATH = (
    os.environ.get("OVERSEAS_TRANSLATE_CACHE_PATH")
    or os.path.join(DATA_DIR, "translate_cache.json"))

# ---------------- 网评 AI：HiGPT / xinghai-ultra ----------------
# API key 和 user key 只从环境变量读取，不写入代码、文档或导出文件。
XINGHAI_BASE_URL = (
    os.environ.get("OVERSEAS_XINGHAI_BASE_URL")
    or "https://inner-apisix.hisense.com/higpt-new/v1"
).strip().rstrip("/")
XINGHAI_API_KEY = (os.environ.get("OVERSEAS_XINGHAI_API_KEY") or "").strip()
XINGHAI_USER_KEY = (os.environ.get("OVERSEAS_XINGHAI_USER_KEY") or "").strip()
XINGHAI_MODEL = (os.environ.get("OVERSEAS_XINGHAI_MODEL") or "xinghai-ultra").strip()
XINGHAI_TIMEOUT = _num("OVERSEAS_XINGHAI_TIMEOUT", 90.0)
XINGHAI_MAX_RETRIES = max(0, _int("OVERSEAS_XINGHAI_MAX_RETRIES", 2))
XINGHAI_MAX_WORKERS = max(1, _int("OVERSEAS_XINGHAI_MAX_WORKERS", 2))
XINGHAI_BATCH_RECORDS = max(1, _int("OVERSEAS_XINGHAI_BATCH_RECORDS", 8))
XINGHAI_MAX_TOKENS = max(128, _int("OVERSEAS_XINGHAI_MAX_TOKENS", 1024))
XINGHAI_MAX_INPUT_CHARS = max(1000, _int("OVERSEAS_XINGHAI_MAX_INPUT_CHARS", 16000))
XINGHAI_TEMPERATURE = _num("OVERSEAS_XINGHAI_TEMPERATURE", 0.1)
XINGHAI_SSL_VERIFY = _flag("OVERSEAS_XINGHAI_SSL_VERIFY", True)
XINGHAI_FALLBACK_ENGINE = (
    os.environ.get("OVERSEAS_XINGHAI_FALLBACK_ENGINE") or "nllb"
).strip().lower()
if XINGHAI_FALLBACK_ENGINE not in {"nllb", "argos"}:
    XINGHAI_FALLBACK_ENGINE = "nllb"


# ---------------- LLM：适配器自动生成（阶段三，区别于网评翻译）----------------
# 支持多 provider：xinghai（内网 HiGPT，user_key 走查询参数）/ deepseek（公网官方）
# / 任意 OpenAI 兼容端点。凭据只从环境变量读取，不写入代码或导出文件。
# 默认沿用 xinghai 便于内网直接用；公网环境可切 deepseek。
LLM_PROVIDER = (os.environ.get("OVERSEAS_LLM_PROVIDER") or "xinghai").strip().lower()
LLM_BASE_URL = (os.environ.get("OVERSEAS_LLM_BASE_URL") or "").strip().rstrip("/")
LLM_API_KEY = (os.environ.get("OVERSEAS_LLM_API_KEY") or "").strip()
LLM_USER_KEY = (os.environ.get("OVERSEAS_LLM_USER_KEY") or "").strip()
LLM_MODEL = (os.environ.get("OVERSEAS_LLM_MODEL") or "").strip()
LLM_TIMEOUT = _num("OVERSEAS_LLM_TIMEOUT", 120.0)
LLM_MAX_RETRIES = max(0, _int("OVERSEAS_LLM_MAX_RETRIES", 2))
LLM_MAX_TOKENS = max(256, _int("OVERSEAS_LLM_MAX_TOKENS", 4096))
LLM_TEMPERATURE = _num("OVERSEAS_LLM_TEMPERATURE", 0.1)
LLM_SSL_VERIFY = _flag("OVERSEAS_LLM_SSL_VERIFY", True)

# provider 默认端点/模型：环境变量未显式覆盖时按 provider 取默认值。
_LLM_PROVIDER_DEFAULTS = {
    "xinghai": {
        "base_url": "https://inner-apisix.hisense.com/higpt-new/v1",
        "model": "xinghai-ultra",
        "uses_user_key": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "uses_user_key": False,
    },
    "openai_compatible": {
        "base_url": "",
        "model": "",
        "uses_user_key": False,
    },
}


def llm_settings() -> dict:
    """按当前 provider 汇总生效的 LLM 配置（环境变量覆盖 provider 默认值）。"""
    provider = LLM_PROVIDER if LLM_PROVIDER in _LLM_PROVIDER_DEFAULTS else "xinghai"
    defaults = _LLM_PROVIDER_DEFAULTS[provider]
    base_url = LLM_BASE_URL or defaults["base_url"]
    model = LLM_MODEL or defaults["model"]
    # xinghai 未单独配 LLM_* 时，复用网评线已有的 XINGHAI_* 凭据，避免重复配置。
    api_key = LLM_API_KEY
    user_key = LLM_USER_KEY
    if provider == "xinghai":
        api_key = api_key or XINGHAI_API_KEY
        user_key = user_key or XINGHAI_USER_KEY
        base_url = base_url or XINGHAI_BASE_URL
        model = model or XINGHAI_MODEL
    return {
        "provider": provider,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "user_key": user_key,
        "model": model,
        "uses_user_key": bool(defaults["uses_user_key"]),
        # xinghai 网关强制流式返回；公网 provider 默认非流式更稳。
        "stream": _flag("OVERSEAS_LLM_STREAM", provider == "xinghai"),
        "timeout": LLM_TIMEOUT,
        "max_retries": LLM_MAX_RETRIES,
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": LLM_TEMPERATURE,
        "ssl_verify": LLM_SSL_VERIFY,
    }


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, RAW_DIR, LOG_DIR, SESSION_DIR, PROFILE_DIR):
        os.makedirs(d, exist_ok=True)


def session_path(site_code: str) -> str:
    """某站点的 storage_state 文件路径（按站点隔离，避免跨站串 cookie）。"""
    code = (site_code or "").strip() or "default"
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in code)
    return os.path.join(SESSION_DIR, f"{safe}.state.json")


def profile_dir(site_code: str) -> str:
    """某站点的**持久化浏览器 profile** 目录。

    与 storage_state 的区别：profile 是真实 Chromium 用户目录，反爬系统更信任，
    登录态也更稳。用 launch_persistent_context 打开后，登录一次即可长期复用。
    """
    code = (site_code or "").strip() or "default"
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in code)
    return os.path.join(PROFILE_DIR, safe)


def storage_enabled() -> bool:
    """是否启用登录态持久化（OVERSEAS_USE_STORAGE_STATE=0 可整体关闭）。"""
    return _flag("OVERSEAS_USE_STORAGE_STATE", True)


def summary() -> dict:
    """给 CLI 展示当前生效配置（不含敏感值明文）。"""
    return {
        "base_dir": BASE_DIR,
        "db_path": DB_PATH,
        "db_url": "(已设置)" if DB_URL else "(未设置, 用 SQLite)",
        "min_interval": MIN_INTERVAL,
        "jitter": JITTER,
        "max_retries": MAX_RETRIES,
        "max_retries_per_request": MAX_RETRIES_PER_REQUEST,
        "max_consecutive_model_failures": MAX_CONSECUTIVE_MODEL_FAILURES,
        "max_consecutive_series_failures": MAX_CONSECUTIVE_SERIES_FAILURES,
        "max_consecutive_search_blocked": MAX_CONSECUTIVE_SEARCH_BLOCKED,
        "max_consecutive_pdp_blocked": MAX_CONSECUTIVE_PDP_BLOCKED,
        "max_consecutive_no_price": MAX_CONSECUTIVE_NO_PRICE,
        "max_consecutive_review_blocked": MAX_CONSECUTIVE_REVIEW_BLOCKED,
        "max_review_pages": MAX_REVIEW_PAGES,
        "max_review_threads": MAX_REVIEW_THREADS,
        "spec_timeout_seconds": SPEC_TIMEOUT_SECONDS,
        "review_timeout_seconds": REVIEW_TIMEOUT_SECONDS,
        "headless": HEADLESS,
        "browser_executable": BROWSER_EXECUTABLE or "(未设, 用系统通道)",
        "browser_channels": BROWSER_CHANNELS,
        "nav_timeout_ms": NAV_TIMEOUT_MS,
        "proxy": PROXY or "(直连)",
        "cache_enabled": CACHE_ENABLED,
        "save_raw_html": SAVE_RAW_HTML,
        "session_dir": SESSION_DIR,
        "use_storage_state": storage_enabled(),
        "xinghai_base_url": XINGHAI_BASE_URL or "(未设置)",
        "xinghai_model": XINGHAI_MODEL or "(未设置)",
        "xinghai_api_key": "(已设置)" if XINGHAI_API_KEY else "(未设置)",
        "xinghai_user_key": "(已设置)" if XINGHAI_USER_KEY else "(未设置)",
        "xinghai_fallback_engine": XINGHAI_FALLBACK_ENGINE,
    }
