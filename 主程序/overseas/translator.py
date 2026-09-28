"""在线翻译接口客户端：词典未命中时的可选回退。

设计要点：
- 标准库 urllib 实现，零第三方依赖（与项目其余部分一致）。
- 默认关闭（config.TRANSLATE_ENABLED），离线词典始终优先。
- 支持 deepl / google / custom 三种提供商。
- 结果缓存到 JSON 文件（config.TRANSLATE_CACHE_PATH），避免重复请求；
  缓存内容是「日文原文 → 中文」，人工审核后可整理进主词典 spec_ja_zh.json。
- 任何网络/解析异常都吞掉并返回空串，绝不因翻译失败中断采集或导出。

用法（在 dict.translate 内部调用）：
    from . import translator
    zh = translator.translate_online(item_ja)   # 未开启或失败时返回 ""
"""
from __future__ import annotations

import json
import os
import threading
import urllib.parse
import urllib.request

from . import config

_cache: dict[str, str] | None = None
_cache_lock = threading.Lock()
_cache_dirty = False


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    path = config.TRANSLATE_CACHE_PATH
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _cache = {str(k): str(v) for k, v in data.items() if v}
        except (OSError, json.JSONDecodeError, ValueError):
            _cache = {}
    else:
        _cache = {}
    return _cache


def flush_cache() -> None:
    """把缓存写回磁盘（进程内批量翻译后调用一次即可）。"""
    global _cache_dirty
    if _cache is None or not _cache_dirty:
        return
    path = config.TRANSLATE_CACHE_PATH
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        _cache_dirty = False
    except OSError:
        pass


def _dig(obj, dotted_path: str):
    """按点分路径从嵌套 dict/list 取值，如 'data.translations.0.text'。"""
    cur = obj
    for part in dotted_path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _http_post(url: str, data: bytes, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=config.TRANSLATE_TIMEOUT) as resp:
        return resp.read()


def _call_deepl(text: str) -> str:
    # DeepL API：免费版 api-free.deepl.com，付费版 api.deepl.com。按 key 后缀判断。
    key = config.TRANSLATE_API_KEY
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    url = f"https://{host}/v2/translate"
    body = urllib.parse.urlencode({
        "text": text,
        "source_lang": config.TRANSLATE_SOURCE,      # JA
        "target_lang": config.TRANSLATE_TARGET,      # ZH
    }).encode("utf-8")
    headers = {
        "Authorization": f"DeepL-Auth-Key {key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    raw = _http_post(url, body, headers)
    obj = json.loads(raw.decode("utf-8"))
    return str(_dig(obj, "translations.0.text") or "")


def _call_google(text: str) -> str:
    # Google Cloud Translation v2：key 作为 query 参数。
    key = config.TRANSLATE_API_KEY
    url = "https://translation.googleapis.com/language/translate/v2?key=" + urllib.parse.quote(key)
    body = json.dumps({
        "q": text,
        "source": config.TRANSLATE_SOURCE.lower(),    # ja
        "target": config.TRANSLATE_TARGET.lower(),    # zh
        "format": "text",
    }).encode("utf-8")
    raw = _http_post(url, body, {"Content-Type": "application/json"})
    obj = json.loads(raw.decode("utf-8"))
    return str(_dig(obj, "data.translations.0.translatedText") or "")


def _call_custom(text: str) -> str:
    # 自定义 HTTP 接口：POST {text, source, target}，从配置的点分路径取结果。
    url = config.TRANSLATE_CUSTOM_URL
    if not url:
        return ""
    body = json.dumps({
        "text": text,
        "source": config.TRANSLATE_SOURCE,
        "target": config.TRANSLATE_TARGET,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.TRANSLATE_API_KEY:
        headers["Authorization"] = f"Bearer {config.TRANSLATE_API_KEY}"
    raw = _http_post(url, body, headers)
    obj = json.loads(raw.decode("utf-8"))
    return str(_dig(obj, config.TRANSLATE_CUSTOM_RESULT_PATH) or "")


_PROVIDERS = {
    "deepl": _call_deepl,
    "google": _call_google,
    "custom": _call_custom,
}


def translate_online(text: str) -> str:
    """在线翻译单个日文项目名，返回中文；未开启/失败/为空一律返回空串。

    先查缓存，命中直接返回；未命中调接口，成功后写入缓存（内存，需 flush_cache 落盘）。
    """
    global _cache_dirty
    if not config.TRANSLATE_ENABLED:
        return ""
    text = (text or "").strip()
    if not text:
        return ""
    cache = _load_cache()
    if text in cache:
        return cache[text]

    fn = _PROVIDERS.get(config.TRANSLATE_PROVIDER)
    if fn is None:
        return ""
    if config.TRANSLATE_PROVIDER != "custom" and not config.TRANSLATE_API_KEY:
        return ""      # 需要 key 的提供商未配置 key，静默跳过

    try:
        zh = (fn(text) or "").strip()
    except Exception:  # noqa: BLE001 — 翻译失败绝不影响主流程
        return ""

    if zh:
        with _cache_lock:
            cache[text] = zh
            _cache_dirty = True
    return zh
