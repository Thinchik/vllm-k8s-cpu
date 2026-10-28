"""Безопасная замена методов vllm RequestLogger.

Оригинал (vllm 0.32.0, vllm/entrypoints/serve/utils/request_logger.py):
  * log_inputs        - на DEBUG пишет prompt и prompt_token_ids, на INFO - SamplingParams целиком
  * log_request_body  - на DEBUG пишет весь JSON запроса
  * log_outputs       - на INFO пишет сгенерированный текст

Что пишем вместо этого:
  * факт запроса: request_id, длина промпта в токенах, n / max_tokens / temperature / top_p / top_k, имя LoRA
  * факт ответа: request_id, число сгенерированных токенов, finish_reason

SamplingParams целиком не логируем: в нем бывают stop-строки, logit_bias
и схемы structured output, то есть фрагменты пользовательских данных.
"""

from __future__ import annotations

import logging
from typing import Any

SAFE_PARAM_FIELDS = ("n", "max_tokens", "temperature", "top_p", "top_k")

_fallback_logger = logging.getLogger("vllm.entrypoints.serve.utils.request_logger")


def summarize_params(params: Any) -> str:
    if params is None:
        return "-"
    parts = [f"type={type(params).__name__}"]
    for field in SAFE_PARAM_FIELDS:
        value = getattr(params, field, None)
        if value is not None:
            parts.append(f"{field}={value}")
    return " ".join(parts)


def _token_count(token_ids: Any) -> int | None:
    if token_ids is None:
        return None
    try:
        return len(token_ids)
    except TypeError:
        return None


def patch_module(module: Any) -> None:
    cls = getattr(module, "RequestLogger", None)
    if cls is None or getattr(cls, "_vllm_privacy_patched", False):
        return
    logger = getattr(module, "logger", _fallback_logger)

    def log_inputs(
        self,
        request_id: str,
        prompt: str | None = None,
        prompt_token_ids: Any = None,
        prompt_embeds: Any = None,
        params: Any = None,
        lora_request: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        logger.info(
            "Request %s received: prompt_tokens=%s, has_embeds=%s, params=[%s], lora=%s",
            request_id,
            _token_count(prompt_token_ids),
            prompt_embeds is not None,
            summarize_params(params),
            getattr(lora_request, "lora_name", None),
        )

    def log_request_body(self, request: Any, *args: Any, **kwargs: Any) -> None:
        # тело запроса не логируем ни на каком уровне
        return None

    def log_outputs(
        self,
        request_id: str,
        outputs: Any = None,
        output_token_ids: Any = None,
        finish_reason: str | None = None,
        is_streaming: bool = False,
        delta: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if is_streaming and delta:
            # на каждый чанк стрима не пишем, достаточно финальной записи
            return
        logger.info(
            "Response %s finished: output_tokens=%s, finish_reason=%s, streaming=%s",
            request_id,
            _token_count(output_token_ids),
            finish_reason,
            is_streaming,
        )

    cls.log_inputs = log_inputs
    cls.log_request_body = log_request_body
    cls.log_outputs = log_outputs
    cls._vllm_privacy_patched = True
    logger.info("vllm_privacy: RequestLogger patched, prompts and outputs are not logged")
