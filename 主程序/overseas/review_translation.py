"""网评翻译：本地日语→中文与 HiGPT 大模型后端。

本模块保留 Argos/NLLB 离线翻译实现，并通过
``build_translation_hooks(engine=...)`` 暴露统一接口；``engine="xinghai"``
会调用 HiGPT 的 OpenAI-compatible Chat Completions，未配置或请求失败时按
配置回退到本地 NLLB/Argos。模型和远程客户端均采用延迟加载，未启用翻译时
不会发起网络请求。
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from .review_export import AiHooks

_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_DEFAULT_CACHE_SIZE = 4096
_DEFAULT_BATCH_RECORDS = 64


class TranslationUnavailable(RuntimeError):
    """Argos 或所需语言模型不可用。"""


@dataclass
class _BatchRuntime:
    """Argos PackageTranslation 的批量推理运行时。"""

    package_translation: Any
    package: Any
    tokenizer: Any
    sentencizer: Any
    translator: Any


@dataclass
class _StageRecord:
    """一个翻译阶段的记录及其段落/句子边界。"""

    key: str
    paragraphs: list[list[str]]


class ArgosJapaneseToChinese:
    """使用 Argos 的日语→英语→中文本地翻译器。

    Argos 模型加载与翻译都放在锁内，避免多品牌并发时重复初始化模型或并发
    访问底层 translator。结果按原文做进程内缓存，重复网评不会重复推理。
    除了单条 ``translate`` 外，``translate_many`` 会把不同网评的句子合并到
    CTranslate2 的 ``translate_batch`` 中，再按原记录和段落边界还原结果。
    """

    def __init__(self, cache_size: int = _DEFAULT_CACHE_SIZE):
        self.cache_size = max(0, int(cache_size))
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.RLock()
        self._ja_en: Any | None = None
        self._en_zh: Any | None = None
        self._load_error: str = ""

    @property
    def available(self) -> bool:
        """返回两个 Argos 翻译模型是否可以加载。"""
        try:
            self._ensure_loaded()
        except TranslationUnavailable:
            return False
        return True

    @property
    def load_error(self) -> str:
        return self._load_error

    @staticmethod
    def _silence_third_party_logs() -> None:
        # stanza（Argos 分句依赖）会打印 "expects mwt, which has been added"
        # 之类的 WARNING，污染任务日志；这里统一压到 ERROR，不影响翻译结果。
        import logging

        for name in ("stanza", "sacremoses"):
            logging.getLogger(name).setLevel(logging.ERROR)

    def _ensure_loaded(self) -> None:
        if self._ja_en is not None and self._en_zh is not None:
            return
        with self._lock:
            if self._ja_en is not None and self._en_zh is not None:
                return
            self._silence_third_party_logs()
            try:
                from argostranslate import translate as argos_translate
            except ImportError as exc:
                self._load_error = "未安装 argostranslate"
                raise TranslationUnavailable(self._load_error) from exc
            try:
                ja_en = argos_translate.get_translation_from_codes("ja", "en")
                en_zh = argos_translate.get_translation_from_codes("en", "zh")
            except Exception as exc:
                self._load_error = (
                    "缺少 Argos 日→英或英→中模型，请运行 "
                    "scripts/install_argos_models.py；"
                    f"{type(exc).__name__}: {exc}"
                )
                raise TranslationUnavailable(self._load_error) from exc
            if ja_en is None or en_zh is None:
                self._load_error = (
                    "缺少 Argos 日→英或英→中模型，请运行 "
                    "scripts/install_argos_models.py"
                )
                raise TranslationUnavailable(self._load_error)
            self._ja_en = ja_en
            self._en_zh = en_zh

    @staticmethod
    def _needs_translation(text: str) -> bool:
        # 没有日文假名时通常是已是中文/英文的内容，保留原文，避免误译。
        return bool(_JAPANESE_RE.search(text or ""))

    @staticmethod
    def _package_translation(translation: Any) -> Any:
        """取得 CachedTranslation 下的 PackageTranslation。"""
        current = translation
        seen: set[int] = set()
        while current is not None and hasattr(current, "underlying"):
            marker = id(current)
            if marker in seen:
                break
            seen.add(marker)
            underlying = getattr(current, "underlying", None)
            if underlying is None:
                break
            current = underlying
        return current

    @classmethod
    def _batch_runtime(cls, translation: Any) -> _BatchRuntime:
        """准备与 Argos 1.11.0 单条实现一致的 CTranslate2 运行时。"""
        package_translation = cls._package_translation(translation)
        package = getattr(package_translation, "pkg", None)
        tokenizer = getattr(package, "tokenizer", None)
        sentencizer = getattr(package_translation, "sentencizer", None)
        if package is None or tokenizer is None or sentencizer is None:
            raise TranslationUnavailable("Argos 翻译模型不支持批量接口")

        ct_translator = getattr(package_translation, "translator", None)
        if ct_translator is None:
            try:
                import ctranslate2
                from argostranslate import settings

                model_path = str(package.package_path / "model")
                ct_translator = ctranslate2.Translator(
                    model_path,
                    device=settings.device,
                    inter_threads=settings.inter_threads,
                    intra_threads=settings.intra_threads,
                    compute_type=settings.compute_type,
                )
                # 让后续单条调用也复用同一个已加载的底层模型。
                package_translation.translator = ct_translator
            except Exception as exc:
                raise TranslationUnavailable(
                    f"Argos 批量模型初始化失败: {type(exc).__name__}: {exc}"
                ) from exc
        return _BatchRuntime(
            package_translation=package_translation,
            package=package,
            tokenizer=tokenizer,
            sentencizer=sentencizer,
            translator=ct_translator,
        )

    @staticmethod
    def _stage_record(key: str, text: str, runtime: _BatchRuntime) -> _StageRecord:
        paragraphs: list[list[str]] = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                paragraphs.append([])
                continue
            sentences = runtime.sentencizer.split_sentences(paragraph)
            sentences = [str(sentence) for sentence in sentences if str(sentence).strip()]
            if not sentences:
                # 个别短文本可能无法被 SBD 识别，沿用整段避免静默丢失。
                sentences = [paragraph]
            paragraphs.append(sentences)
        return _StageRecord(key=key, paragraphs=paragraphs)

    @classmethod
    def _build_stage_records(
        cls,
        items: list[tuple[str, str]],
        runtime: _BatchRuntime,
    ) -> tuple[list[_StageRecord], set[str]]:
        records: list[_StageRecord] = []
        failed: set[str] = set()
        for key, text in items:
            try:
                records.append(cls._stage_record(key, text, runtime))
            except Exception:
                # 分句失败只影响当前网评，其他记录仍可继续批量翻译。
                failed.add(key)
        return records, failed

    @staticmethod
    def _clean_stage_value(value: str, runtime: _BatchRuntime) -> str:
        prefix = getattr(runtime.package, "target_prefix", "")
        if prefix and value.startswith(prefix):
            value = value[len(prefix):]
        if value.startswith(" "):
            value = value[1:]
        return value

    @classmethod
    def _translate_stage_group(
        cls,
        records: list[_StageRecord],
        runtime: _BatchRuntime,
        max_tokens_per_batch: int,
    ) -> dict[str, str]:
        """一次 CTranslate2 批量调用并按记录/段落重建文本。"""
        tokenized: list[list[str]] = []
        locations: list[tuple[str, int]] = []
        for record in records:
            for paragraph_index, sentences in enumerate(record.paragraphs):
                for sentence in sentences:
                    tokens = runtime.tokenizer.encode(sentence)
                    if tokens:
                        tokenized.append(tokens)
                        locations.append((record.key, paragraph_index))

        if not tokenized:
            return {record.key: "\n".join("" for _ in record.paragraphs) for record in records}

        target_prefix = getattr(runtime.package, "target_prefix", "")
        target_prefixes = None
        if target_prefix:
            target_prefixes = [[target_prefix]] * len(tokenized)
        try:
            from argostranslate import settings

            translated = runtime.translator.translate_batch(
                tokenized,
                target_prefix=target_prefixes,
                replace_unknowns=True,
                max_batch_size=max_tokens_per_batch,
                batch_type="tokens",
                beam_size=max(1, settings.beam_size),
                num_hypotheses=1,
                length_penalty=0.2,
                return_scores=True,
            )
        except Exception:
            raise
        if len(translated) != len(locations):
            raise RuntimeError("Argos 批量翻译返回数量与输入句子不一致")

        paragraph_tokens: dict[tuple[str, int], list[str]] = {}
        for location, result in zip(locations, translated):
            hypotheses = getattr(result, "hypotheses", None)
            if not hypotheses:
                raise RuntimeError("Argos 批量翻译返回空结果")
            paragraph_tokens.setdefault(location, []).extend(hypotheses[0])

        output: dict[str, str] = {}
        for record in records:
            paragraphs: list[str] = []
            for paragraph_index in range(len(record.paragraphs)):
                tokens = paragraph_tokens.get((record.key, paragraph_index), [])
                value = runtime.tokenizer.decode(tokens) if tokens else ""
                paragraphs.append(cls._clean_stage_value(value, runtime))
            output[record.key] = "\n".join(paragraphs)
        return output

    @classmethod
    def _translate_stage(
        cls,
        records: list[_StageRecord],
        runtime: _BatchRuntime,
        max_tokens_per_batch: int,
    ) -> tuple[dict[str, str], set[str]]:
        """批量翻译；某批失败时递归拆分，最终只丢弃失败网评。"""
        translated: dict[str, str] = {}
        failed: set[str] = set()

        def process(group: list[_StageRecord]) -> None:
            if not group:
                return
            try:
                translated.update(cls._translate_stage_group(group, runtime, max_tokens_per_batch))
            except Exception:
                if len(group) == 1:
                    failed.add(group[0].key)
                    return
                midpoint = len(group) // 2
                process(group[:midpoint])
                process(group[midpoint:])

        process(records)
        return translated, failed

    def translate(self, text: str) -> str:
        """翻译一条日文网评；非日文文本原样返回。"""
        text = str(text or "").strip()
        if not text:
            return ""
        if not self._needs_translation(text):
            return text
        with self._lock:
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                return cached
            self._ensure_loaded()
            try:
                english = self._ja_en.translate(text)
                result = self._en_zh.translate(english) if english else ""
            except Exception as exc:
                raise TranslationUnavailable(
                    f"Argos 翻译失败: {type(exc).__name__}: {exc}"
                ) from exc
            result = str(result or "").strip()
            if self.cache_size > 0:
                self._cache[text] = result
                self._cache.move_to_end(text)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            return result

    def translate_many(
        self,
        texts: list[str],
        max_tokens_per_batch: int | None = None,
        max_records_per_batch: int = _DEFAULT_BATCH_RECORDS,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        """跨网评批量执行日→英→中翻译并保持输入顺序。

        ``max_tokens_per_batch`` 沿用 Argos 的 token 预算；``max_records_per_batch``
        只限制一次聚合的唯一原文数量。批量失败时会递归拆分到单条，避免一条
        异常文本导致整批结果丢失。``progress`` 接收“已处理原记录数、总数”。
        """
        values = [str(text or "").strip() for text in texts]
        results = ["" for _ in values]
        pending: OrderedDict[str, list[int]] = OrderedDict()
        processed = 0

        with self._lock:
            for index, text in enumerate(values):
                if not text:
                    processed += 1
                    continue
                if not self._needs_translation(text):
                    results[index] = text
                    processed += 1
                    continue
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    results[index] = cached
                    processed += 1
                    continue
                pending.setdefault(text, []).append(index)

            if not pending:
                if progress:
                    progress(processed, len(values))
                return results

            self._ensure_loaded()
            try:
                from argostranslate import settings

                default_tokens = int(settings.batch_size)
            except Exception as exc:
                raise TranslationUnavailable(
                    f"无法读取 Argos 批量设置: {type(exc).__name__}: {exc}"
                ) from exc
            max_tokens = int(max_tokens_per_batch or default_tokens)
            if max_tokens <= 0:
                max_tokens = default_tokens
            max_records = max(1, int(max_records_per_batch))
            ja_runtime = self._batch_runtime(self._ja_en)
            en_runtime = self._batch_runtime(self._en_zh)

            pending_items = list(pending.items())
            total = len(values)
            for start in range(0, len(pending_items), max_records):
                chunk = pending_items[start:start + max_records]
                stage1_records, stage1_build_failed = self._build_stage_records(
                    [(key, key) for key, _indexes in chunk], ja_runtime
                )
                english, stage1_translate_failed = self._translate_stage(
                    stage1_records, ja_runtime, max_tokens
                )
                stage1_failed = stage1_build_failed | stage1_translate_failed

                stage2_items = [
                    (key, value) for key, value in english.items() if key not in stage1_failed
                ]
                stage2_records, stage2_build_failed = self._build_stage_records(
                    stage2_items, en_runtime
                )
                chinese, stage2_translate_failed = self._translate_stage(
                    stage2_records, en_runtime, max_tokens
                )
                failed = stage1_failed | stage2_build_failed | stage2_translate_failed

                for key, indexes in chunk:
                    result = "" if key in failed else str(chinese.get(key, "") or "").strip()
                    for index in indexes:
                        results[index] = result
                    if key not in failed and self.cache_size > 0:
                        self._cache[key] = result
                        self._cache.move_to_end(key)
                        while len(self._cache) > self.cache_size:
                            self._cache.popitem(last=False)
                    processed += len(indexes)
                if progress:
                    progress(processed, total)

        return results


def build_argos_hooks(cache_size: int = _DEFAULT_CACHE_SIZE) -> tuple[AiHooks, ArgosJapaneseToChinese]:
    """创建启用离线日译中的 AiHooks 和其翻译器实例。"""
    translator = ArgosJapaneseToChinese(cache_size=cache_size)
    # 这里先验证模型，调用方可以在任务开始时立即报告配置错误，而不是等到
    # 导出阶段才发现翻译列为空。
    translator._ensure_loaded()
    return AiHooks(translate=translator.translate), translator


# ==================== NLLB-200 本地直连日→中 ====================
#
# Argos 需 ja→en→zh 两段接力，长句/重复结构会退化（复读、□ 未知字符）。
# NLLB-200 支持日语(jpn_Jpan)→简体中文(zho_Hans)直连，质量更好。本类保持与
# ArgosJapaneseToChinese 相同的公开接口（available / translate / translate_many /
# cache_size），可被回填脚本与导出层直接替换。运行时只读本机已下载模型。

_NLLB_MODEL_NAME = "facebook/nllb-200-distilled-600M"
_NLLB_SRC_LANG = "jpn_Jpan"
_NLLB_TGT_LANG = "zho_Hans"
_NLLB_MAX_TOKENS = 512
# beam 数：实测 beam=2 质量接近 beam=4，速度快约 2 倍，作为默认折中值。
_NLLB_NUM_BEAMS = 2


class NllbJapaneseToChinese:
    """使用本地 NLLB-200 模型直接把日语网评翻译成简体中文。

    - 直接 ja→zh，不绕英语，避免两段接力的语义损失与复读；
    - ``translate`` 单条、``translate_many`` 跨网评批量（走 ``model.generate``）；
    - 与 Argos 版一致：空值/非日文原样处理、进程内 LRU 缓存、失败降级。
    """

    def __init__(self, cache_size: int = _DEFAULT_CACHE_SIZE,
                 model_name: str = _NLLB_MODEL_NAME,
                 num_beams: int = _NLLB_NUM_BEAMS):
        self.cache_size = max(0, int(cache_size))
        self.model_name = model_name
        self.num_beams = max(1, int(num_beams))
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.RLock()
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._bos_id: int | None = None
        self._load_error: str = ""

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
        if self._model is not None and self._tokenizer is not None:
            return
        with self._lock:
            if self._model is not None and self._tokenizer is not None:
                return
            try:
                from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            except ImportError as exc:
                self._load_error = "未安装 transformers（NLLB 后端所需）"
                raise TranslationUnavailable(self._load_error) from exc
            try:
                tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
                model.eval()
            except Exception as exc:
                self._load_error = (
                    f"加载 NLLB 模型失败（{self.model_name}）：请先联网下载或放置本地缓存；"
                    f"{type(exc).__name__}: {exc}"
                )
                raise TranslationUnavailable(self._load_error) from exc
            tokenizer.src_lang = _NLLB_SRC_LANG
            try:
                bos_id = tokenizer.convert_tokens_to_ids(_NLLB_TGT_LANG)
            except Exception as exc:
                self._load_error = f"NLLB 目标语言标记不可用: {exc}"
                raise TranslationUnavailable(self._load_error) from exc
            self._tokenizer = tokenizer
            self._model = model
            self._bos_id = bos_id

    @staticmethod
    def _needs_translation(text: str) -> bool:
        return bool(_JAPANESE_RE.search(text or ""))

    def _generate(self, texts: list[str]) -> list[str]:
        """对一批日文句子直接生成中文；调用方保证均为需翻译文本。"""
        import torch

        tokenizer = self._tokenizer
        model = self._model
        tokenizer.src_lang = _NLLB_SRC_LANG
        encoded = tokenizer(texts, return_tensors="pt", padding=True,
                            truncation=True, max_length=_NLLB_MAX_TOKENS)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=self._bos_id,
                max_length=_NLLB_MAX_TOKENS,
                num_beams=self.num_beams,
                no_repeat_ngram_size=3,   # 抑制复读（如“相相相”）
            )
        return [str(s or "").strip()
                for s in tokenizer.batch_decode(generated, skip_special_tokens=True)]

    def translate(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if not self._needs_translation(text):
            return text
        with self._lock:
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                return cached
            self._ensure_loaded()
            try:
                result = self._generate([text])[0]
            except Exception as exc:
                raise TranslationUnavailable(
                    f"NLLB 翻译失败: {type(exc).__name__}: {exc}"
                ) from exc
            if self.cache_size > 0:
                self._cache[text] = result
                self._cache.move_to_end(text)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)
            return result

    def translate_many(
        self,
        texts: list[str],
        max_tokens_per_batch: int | None = None,   # 兼容签名，NLLB 用记录数分批
        max_records_per_batch: int = 16,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[str]:
        """跨网评批量直译日→中，保持输入顺序；失败降级为逐条再降为留空。"""
        values = [str(text or "").strip() for text in texts]
        results = ["" for _ in values]
        pending: OrderedDict[str, list[int]] = OrderedDict()
        processed = 0

        with self._lock:
            for index, text in enumerate(values):
                if not text:
                    processed += 1
                    continue
                if not self._needs_translation(text):
                    results[index] = text
                    processed += 1
                    continue
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    results[index] = cached
                    processed += 1
                    continue
                pending.setdefault(text, []).append(index)

            if not pending:
                if progress:
                    progress(processed, len(values))
                return results

            self._ensure_loaded()
            max_records = max(1, int(max_records_per_batch))
            unique_texts = list(pending.keys())
            total = len(values)

            for start in range(0, len(unique_texts), max_records):
                chunk = unique_texts[start:start + max_records]
                try:
                    outputs = self._generate(chunk)
                except Exception:
                    # 整批失败时退回逐条，单条再失败则该条留空，不影响其它记录。
                    outputs = []
                    for one in chunk:
                        try:
                            outputs.append(self._generate([one])[0])
                        except Exception:
                            outputs.append("")
                for text, output in zip(chunk, outputs):
                    result = str(output or "").strip()
                    for index in pending[text]:
                        results[index] = result
                    if result and self.cache_size > 0:
                        self._cache[text] = result
                        self._cache.move_to_end(text)
                        while len(self._cache) > self.cache_size:
                            self._cache.popitem(last=False)
                    processed += len(pending[text])
                if progress:
                    progress(processed, total)

        return results


def build_nllb_hooks(cache_size: int = _DEFAULT_CACHE_SIZE) -> tuple[AiHooks, NllbJapaneseToChinese]:
    """创建启用本地 NLLB 直连日→中的 AiHooks 和翻译器实例。"""
    translator = NllbJapaneseToChinese(cache_size=cache_size)
    translator._ensure_loaded()
    return AiHooks(translate=translator.translate), translator


def build_translation_hooks(engine: str = "nllb",
                            cache_size: int = _DEFAULT_CACHE_SIZE):
    """按引擎名构造翻译/AI 钩子与处理器。

    ``nllb``（默认）使用本地 NLLB 直连日→中；``argos`` 使用旧的
    ja→en→zh 两段链；``xinghai`` 使用 HiGPT 的 OpenAI-compatible
    Chat Completions，并在未配置/请求失败时按配置回退到离线引擎。
    """
    engine = (engine or "nllb").strip().lower()
    if engine == "argos":
        return build_argos_hooks(cache_size=cache_size)
    if engine == "nllb":
        return build_nllb_hooks(cache_size=cache_size)
    if engine in {"xinghai", "xinghai-ultra", "llm"}:
        from .xinghai_translation import build_xinghai_hooks
        return build_xinghai_hooks(cache_size=cache_size)
    raise ValueError(f"未知翻译引擎: {engine}（可选 nllb、argos、xinghai）")
