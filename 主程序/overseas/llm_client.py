"""通用 LLM Chat 客户端（多 provider，OpenAI 兼容）。

支持三类 provider（config.LLM_PROVIDER）：
    - xinghai：内网 HiGPT/xinghai-ultra，user_key 走查询参数（extra_query）；
    - deepseek：公网 DeepSeek 官方 API（https://api.deepseek.com）；
    - openai_compatible：任意 OpenAI 兼容端点。

优先使用 openai SDK（实测内网/公网均以此方式调通）；未安装 SDK 时回退到
urllib（仅对不需要 user_key 的标准端点可靠）。凭据只从环境变量读取，不写入
代码、日志或请求路径；xinghai 的 user_key 按约定作查询参数发送。

用途：阶段三"适配器自动生成"的底层调用，与网评翻译 xinghai_translation.py 解耦。
"""
from __future__ import annotations

import json
import ssl
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from . import config

_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class LLMUnavailable(RuntimeError):
    """LLM 未配置或请求失败。"""


class LLMClient:
    """最小多 provider Chat Completions 客户端。"""

    def __init__(self, settings: dict | None = None, **overrides: Any):
        cfg = dict(settings or config.llm_settings())
        cfg.update({k: v for k, v in overrides.items() if v is not None})
        self.provider = str(cfg.get("provider") or "xinghai")
        self.base_url = str(cfg.get("base_url") or "").rstrip("/")
        self.api_key = str(cfg.get("api_key") or "")
        self.user_key = str(cfg.get("user_key") or "")
        self.model_name = str(cfg.get("model") or "")
        self.uses_user_key = bool(cfg.get("uses_user_key"))
        self.timeout = max(1.0, float(cfg.get("timeout") or 120.0))
        self.max_retries = max(0, int(cfg.get("max_retries") or 2))
        self.max_tokens = max(256, int(cfg.get("max_tokens") or 4096))
        self.temperature = float(cfg.get("temperature") if cfg.get("temperature") is not None else 0.1)
        self.ssl_verify = bool(cfg.get("ssl_verify", True))
        # 默认非流式：更简单可靠；某些环境下流式迭代器行为异常。
        # 内网 xinghai 若强制流式，可传 stream=True 覆盖。
        self.stream = bool(cfg.get("stream", False))

    @property
    def configured(self) -> bool:
        ok = bool(self.base_url and self.api_key and self.model_name)
        if self.uses_user_key:
            ok = ok and bool(self.user_key)
        return ok

    def _ensure(self) -> None:
        if not self.configured:
            need = "OVERSEAS_LLM_PROVIDER / OVERSEAS_LLM_API_KEY / OVERSEAS_LLM_MODEL"
            if self.uses_user_key:
                need += " / OVERSEAS_LLM_USER_KEY"
            raise LLMUnavailable(f"LLM 未配置完整（provider={self.provider}），需设置 {need}")

    def describe(self) -> str:
        return f"provider={self.provider} model={self.model_name} base={self.base_url}"

    # ---- 优先路径：openai SDK ----
    def _chat_openai_sdk(self, messages, temperature, max_tokens) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            return ""  # SDK 不可用，交给 urllib 回退
        kwargs: dict[str, Any] = {}
        if not self.ssl_verify:
            try:
                import httpx
                kwargs["http_client"] = httpx.Client(verify=False, timeout=self.timeout)
            except Exception:
                pass
        client = OpenAI(base_url=self.base_url, api_key=self.api_key,
                        timeout=self.timeout, max_retries=self.max_retries, **kwargs)
        create_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": self.stream,
        }
        if self.uses_user_key and self.user_key:
            create_kwargs["extra_query"] = {"user_key": self.user_key}
        resp = client.chat.completions.create(**create_kwargs)
        if not self.stream:
            try:
                return (resp.choices[0].message.content or "").strip()
            except Exception:
                return ""
        parts: list[str] = []
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                parts.append(chunk.choices[0].delta.content)
        return "".join(parts).strip()

    # ---- 回退路径：urllib（无 user_key 的标准端点）----
    def _endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        if not (self.uses_user_key and self.user_key):
            return endpoint
        parts = urlsplit(endpoint)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "user_key"]
        query.append(("user_key", self.user_key))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _ssl_context(self) -> ssl.SSLContext:
        if self.ssl_verify:
            return ssl.create_default_context()
        return ssl._create_unverified_context()

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
            inner = value.get("text") or value.get("content") or ""
            return inner if isinstance(inner, str) else ""
        return ""

    @classmethod
    def _choice_content(cls, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] or {}
            if isinstance(choice, dict):
                for item in (choice.get("delta") or {}, choice.get("message") or {}, choice):
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
            raise LLMUnavailable("LLM 返回空响应")
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
                    continue
                if isinstance(payload, dict):
                    pieces.append(cls._choice_content(payload))
            result = "".join(pieces).strip()
            if result:
                return result
            raise LLMUnavailable("LLM SSE 响应没有可用文本")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMUnavailable("LLM 返回内容不是有效 JSON/SSE") from exc
        if not isinstance(payload, dict):
            raise LLMUnavailable("LLM 返回 JSON 结构异常")
        result = cls._choice_content(payload).strip()
        if not result:
            raise LLMUnavailable("LLM 返回中没有 choices.content")
        return result

    def _chat_urllib(self, messages, temperature, max_tokens) -> str:
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
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
                with urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
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
                raise LLMUnavailable(f"LLM HTTP {status}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0 ** attempt, 8.0))
                    continue
                raise LLMUnavailable(f"LLM 网络请求失败：{type(exc).__name__}") from exc
        raise LLMUnavailable(
            f"LLM 请求失败：{type(last_error).__name__ if last_error else 'unknown'}")

    def chat(self, messages: list[dict[str, str]],
             temperature: float | None = None,
             max_tokens: int | None = None) -> str:
        """发一轮对话，返回助手文本。SDK 优先，失败回退 urllib，均失败抛 LLMUnavailable。"""
        self._ensure()
        temp = self.temperature if temperature is None else float(temperature)
        maxt = self.max_tokens if max_tokens is None else int(max_tokens)
        sdk_error: Exception | None = None
        try:
            result = self._chat_openai_sdk(messages, temp, maxt)
            if result:
                return result
        except Exception as exc:  # SDK 报错则记录并尝试 urllib
            sdk_error = exc
        try:
            return self._chat_urllib(messages, temp, maxt)
        except LLMUnavailable:
            if sdk_error is not None:
                raise LLMUnavailable(
                    f"LLM 调用失败（SDK: {type(sdk_error).__name__}，urllib 亦失败）")
            raise
