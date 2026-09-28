"""HiGPT/xinghai-ultra 网评 AI 后端。

服务端使用 OpenAI-compatible Chat Completions 接口。凭据只从环境变量读取：
``OVERSEAS_XINGHAI_API_KEY``、``OVERSEAS_XINGHAI_USER_KEY``；本模块不会把
凭据写入日志、文件或请求 URL 的路径中。HiGPT 网关的 user_key 按历史约定
作为查询参数发送，响应同时兼容 SSE 流和普通 JSON。
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from . import config
from .review_export import AiHooks
from .review_translation import (
    TranslationUnavailable,
    _DEFAULT_CACHE_SIZE,
    _JAPANESE_RE,
)

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_DEFAULT_MAX_INPUT_CHARS = 16000


class XinghaiUltraJapaneseToChinese:
    """调用 xinghai-ultra 将日语网评翻译为简体中文并提炼 AI 字段。

    对外接口与本地翻译器兼容：``translate``、``translate_many``、
    ``available``、``load_error``、``cache_size``。Chat Completions 本身不
    提供稳定的多文本批量协议，因此批量接口按唯一原文做受控并发请求，仍保留
    输入顺序、重复文本合并、LRU 缓存和进度回调。
    """

    def __init__(
        self,
        cache_size: int = _DEFAULT_CACHE_SIZE,
        model_name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        user_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_workers: int | None = None,
        fallback_factory: Callable[[], Any] | None = None,
    ):
        self.cache_size = max(0, int(cache_size))
        self.model_name = (model_name or config.XINGHAI_MODEL).strip()
        self.base_url = (base_url or config.XINGHAI_BASE_URL).strip()
        self.api_key = (api_key if api_key is not None else config.XINGHAI_API_KEY).strip()
        self.user_key = (user_key if user_key is not None else config.XINGHAI_USER_KEY).strip()
        self.timeout = max(1.0, float(timeout if timeout is not None else config.XINGHAI_TIMEOUT))
        self.max_retries = max(
            0,
            int(max_retries if max_retries is not None else config.XINGHAI_MAX_RETRIES),
        )
        self.max_workers = max(
            1,
            int(max_workers if max_workers is not None else config.XINGHAI_MAX_WORKERS),
        )
        self.max_input_chars = max(1000, int(config.XINGHAI_MAX_INPUT_CHARS))
        self.max_tokens = max(128, int(config.XINGHAI_MAX_TOKENS))
        self.temperature = float(config.XINGHAI_TEMPERATURE)
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.RLock()
        self._fallback_lock = threading.Lock()
        self._fallback_factory = fallback_factory
        self._fallback: Any | None = None
        self._fallback_attempted = False
        self._load_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.user_key and self.model_name)

    @property
    def available(self) -> bool:
        try:
            self._ensure_loaded()
        except TranslationUnavailable:
            return False
        return True

    @property
    def load_error(self) -> str:
        return self._load_error

    def _ensure_loaded(self) -> None:
        """校验远程配置；不主动发送探测请求，避免无意义消耗接口额度。"""
        if self.configured:
            return
        self._load_error = (
            "xinghai-ultra 未配置完整，请设置 "
            "OVERSEAS_XINGHAI_BASE_URL、OVERSEAS_XINGHAI_API_KEY、"
            "OVERSEAS_XINGHAI_USER_KEY 和 OVERSEAS_XINGHAI_MODEL"
        )
        raise TranslationUnavailable(self._load_error)

    @staticmethod
    def _content_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    part = item.get("text") or item.get("content")
                    if isinstance(part, str):
                        parts.append(part)
            return "".join(parts)
        if isinstance(value, dict):
            value = value.get("text") or value.get("content") or ""
            return value if isinstance(value, str) else ""
        return ""

    @classmethod
    def _choice_content(cls, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] or {}
            if isinstance(choice, dict):
                delta = choice.get("delta") or {}
                message = choice.get("message") or {}
                for item in (delta, message, choice):
                    if isinstance(item, dict):
                        content = cls._content_value(item.get("content"))
                        if content:
                            return content
                        text = cls._content_value(item.get("text"))
                        if text:
                            return text
        for key in ("output_text", "text", "content"):
            content = cls._content_value(payload.get(key))
            if content:
                return content
        return ""

    @classmethod
    def _parse_response(cls, raw: bytes) -> str:
        body = raw.decode("utf-8", errors="replace").strip()
        if not body:
            raise TranslationUnavailable("xinghai-ultra 返回空响应")

        # HiGPT 通常返回 SSE；兼容 data: JSON、[DONE] 和普通 JSON。
        if any(line.lstrip().startswith("data:") for line in body.splitlines()):
            pieces: list[str] = []
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                item = line[5:].strip()
                if not item or item == "[DONE]":
                    continue
                try:
                    payload = json.loads(item)
                except json.JSONDecodeError:
                    # 单个 SSE 数据帧损坏时跳过该帧，最终为空再报错。
                    continue
                if isinstance(payload, dict):
                    pieces.append(cls._choice_content(payload))
            result = "".join(pieces).strip()
            if result:
                return result
            raise TranslationUnavailable("xinghai-ultra SSE 响应没有可用文本")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise TranslationUnavailable("xinghai-ultra 返回内容不是有效 JSON/SSE") from exc
        if not isinstance(payload, dict):
            raise TranslationUnavailable("xinghai-ultra 返回 JSON 结构异常")
        result = cls._choice_content(payload).strip()
        if not result:
            raise TranslationUnavailable("xinghai-ultra 返回中没有 choices.content")
        return result

    def _endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        parts = urlsplit(endpoint)
        query = [(key, value) for key, value in parse_qsl(parts.query) if key != "user_key"]
        query.append(("user_key", self.user_key))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _ssl_context(self) -> ssl.SSLContext:
        if config.XINGHAI_SSL_VERIFY:
            return ssl.create_default_context()
        # 仅供已确认的内网证书环境临时使用；默认永远保持证书校验。
        return ssl._create_unverified_context()

    def _request_chat(self, messages: list[dict[str, str]]) -> str:
        self._ensure_loaded()
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        endpoint = self._endpoint()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            request = Request(endpoint, data=body, headers=headers, method="POST")
            try:
                with urlopen(
                    request,
                    timeout=self.timeout,
                    context=self._ssl_context(),
                ) as response:
                    return self._parse_response(response.read())
            except HTTPError as exc:
                last_error = exc
                status = int(exc.code)
                try:
                    exc.read(512)
                except Exception:
                    pass
                if status in _RETRYABLE_STATUS and attempt < self.max_retries:
                    time.sleep(min(2.0 ** attempt, 8.0))
                    continue
                raise TranslationUnavailable(f"xinghai-ultra HTTP {status}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0 ** attempt, 8.0))
                    continue
                raise TranslationUnavailable(
                    f"xinghai-ultra 网络请求失败：{type(exc).__name__}"
                ) from exc
            except TranslationUnavailable:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0 ** attempt, 8.0))
                    continue
                raise TranslationUnavailable(
                    f"xinghai-ultra 请求失败：{type(exc).__name__}"
                ) from exc

        raise TranslationUnavailable(
            f"xinghai-ultra 请求失败：{type(last_error).__name__ if last_error else 'unknown'}"
        )

    def _get_fallback(self) -> Any | None:
        if self._fallback_factory is None:
            return None
        with self._fallback_lock:
            if self._fallback_attempted:
                return self._fallback
            self._fallback_attempted = True
            try:
                self._fallback = self._fallback_factory()
            except Exception as exc:
                self._load_error = f"xinghai-ultra 回退引擎不可用：{type(exc).__name__}"
                self._fallback = None
            return self._fallback

    def _fallback_or_raise(self, text: str, error: Exception) -> str:
        fallback = self._get_fallback()
        if fallback is not None:
            try:
                result = fallback.translate(text)
                if result:
                    return str(result).strip()
            except Exception as fallback_error:
                self._load_error = (
                    "xinghai-ultra 请求失败且离线回退失败："
                    f"{type(fallback_error).__name__}"
                )
        if isinstance(error, TranslationUnavailable):
            raise error
        raise TranslationUnavailable(
            f"xinghai-ultra 翻译失败：{type(error).__name__}"
        ) from error

    def _review_messages(self, instruction: str, text: str) -> list[dict[str, str]]:
        source = str(text or "").strip()
        if len(source) > self.max_input_chars:
            source = source[: self.max_input_chars]
        return [
            {
                "role": "system",
                "content": (
                    "你是严谨的日语网评处理助手。必须忠实于原文，不得编造信息；"
                    "只输出用户要求的结果，不要输出分析过程。"
                ),
            },
            {
                "role": "user",
                "content": f"{instruction}\n<review>\n{source}\n</review>",
            },
        ]

    def _translate_remote(self, text: str) -> str:
        return self._request_chat(
            self._review_messages(
                "请将下面的日语网评翻译成自然、准确的简体中文，只输出中文译文。",
                text,
            )
        ).strip()

    def _cache_get(self, text: str) -> str | None:
        with self._lock:
            value = self._cache.get(text)
            if value is not None:
                self._cache.move_to_end(text)
            return value

    def _cache_put(self, text: str, value: str) -> None:
        if not value or self.cache_size <= 0:
            return
        with self._lock:
            self._cache[text] = value
            self._cache.move_to_end(text)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    def translate(self, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        if not _JAPANESE_RE.search(value):
            return value
        cached = self._cache_get(value)
        if cached is not None:
            return cached
        try:
            result = self._translate_remote(value)
        except Exception as exc:
            result = self._fallback_or_raise(value, exc)
        result = str(result or "").strip()
        self._cache_put(value, result)
        return result

    def translate_many(
        self,
        texts: list[str],
        max_tokens_per_batch: int | None = None,
        max_records_per_batch: int = 16,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        del max_tokens_per_batch  # 保持与本地翻译器兼容；Chat API 不使用该预算。
        values = [str(text or "").strip() for text in texts]
        results = ["" for _ in values]
        pending: OrderedDict[str, list[int]] = OrderedDict()
        processed = 0
        for index, value in enumerate(values):
            if not value:
                processed += 1
            elif not _JAPANESE_RE.search(value):
                results[index] = value
                processed += 1
            else:
                cached = self._cache_get(value)
                if cached is not None:
                    results[index] = cached
                    processed += 1
                else:
                    pending.setdefault(value, []).append(index)

        if not pending:
            if progress:
                progress(processed, len(values))
            return results
        self._ensure_loaded()
        batch_size = max(1, int(max_records_per_batch or config.XINGHAI_BATCH_RECORDS))

        def request_one(value: str) -> str:
            try:
                return self._translate_remote(value)
            except Exception as exc:
                try:
                    return self._fallback_or_raise(value, exc)
                except Exception:
                    return ""

        unique_values = list(pending)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            for start in range(0, len(unique_values), batch_size):
                chunk = unique_values[start : start + batch_size]
                futures = {pool.submit(request_one, value): value for value in chunk}
                for future in as_completed(futures):
                    value = futures[future]
                    try:
                        result = str(future.result() or "").strip()
                    except Exception:
                        result = ""
                    self._cache_put(value, result)
                    for index in pending[value]:
                        results[index] = result
                    processed += len(pending[value])
                    if progress:
                        progress(processed, len(values))
        return results

    @staticmethod
    def _clean_ai_value(value: str) -> str:
        """把模型表示“没有明确内容”的占位词统一为空，避免污染导出列。"""
        text = str(value or "").strip()
        unquoted = text.strip(" `\\\"'\\t\\r\\n")
        normalized = unquoted.strip("。！!，,：:；; ")
        empty_markers = {
            "",
            "无",
            "没有",
            "暂无",
            "空",
            "空字符串",
            "无明确优点",
            "无明确缺点",
            "无明显优点",
            "无明显缺点",
            "没有明确的优点",
            "没有明确的缺点",
            "没有提及明确的优点",
            "没有提及明确的缺点",
        }
        return "" if normalized in empty_markers else text

    def _extract(self, kind: str, text: str) -> str:
        if not str(text or "").strip():
            return ""
        instruction = {
            "pros": "请从下面的网评中提炼明确提到的产品优点，输出不超过3条的简短中文要点；没有明确优点时只输出空字符串。",
            "cons": "请从下面的网评中提炼明确提到的产品缺点，输出不超过3条的简短中文要点；没有明确缺点时只输出空字符串。",
        }[kind]
        return self._clean_ai_value(
            self._request_chat(self._review_messages(instruction, text))
        )

    def extract_pros(self, text: str) -> str:
        return self._extract("pros", text)

    def extract_cons(self, text: str) -> str:
        return self._extract("cons", text)


def build_xinghai_hooks(
    cache_size: int = _DEFAULT_CACHE_SIZE,
) -> tuple[AiHooks, Any]:
    """创建 xinghai hooks；未配置凭据时回退到配置的离线引擎。"""
    from .review_translation import build_translation_hooks

    fallback_engine = config.XINGHAI_FALLBACK_ENGINE

    def fallback_factory() -> Any:
        _hooks, fallback = build_translation_hooks(
            engine=fallback_engine,
            cache_size=cache_size,
        )
        return fallback

    translator = XinghaiUltraJapaneseToChinese(
        cache_size=cache_size,
        fallback_factory=fallback_factory,
    )
    if not translator.configured:
        # 没有凭据时不让导出链失效，直接使用 NLLB/Argos；已配置但请求失败时
        # 则由 translator 的 fallback_factory 按同一规则按需回退。
        fallback_hooks, fallback = build_translation_hooks(
            engine=fallback_engine,
            cache_size=cache_size,
        )
        return fallback_hooks, fallback
    translator._ensure_loaded()
    return AiHooks(
        translate=translator.translate,
        extract_pros=translator.extract_pros,
        extract_cons=translator.extract_cons,
    ), translator
