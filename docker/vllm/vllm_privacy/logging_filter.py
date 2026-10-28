"""Фильтр logging-а, второй рубеж защиты.

Подключается к handler-у в logging_config.json. Правила:
  * записи уровня ниже INFO от ``vllm.entrypoints.*`` отбрасываются целиком:
    именно там на DEBUG логируются тела запросов (например, эндпоинт /v1/messages);
  * записи RequestLogger в оригинальном формате (с ``prompt:`` / ``output:``)
    отбрасываются - значит, патч не применился;
  * у ошибок из ``vllm.entrypoints.*`` убирается traceback и текст исключения:
    pydantic ValidationError и похожие ошибки содержат фрагменты входных данных.
    Остается только тип исключения.
"""

from __future__ import annotations

import logging

from vllm_privacy import REQUEST_LOGGER_MODULE

_ENTRYPOINTS_PREFIX = "vllm.entrypoints"
_UNPATCHED_MARKERS = ("prompt: %r", "output: %r", "JSON body")


class PrivacyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name or ""
        if not name.startswith(_ENTRYPOINTS_PREFIX):
            return True

        if record.levelno < logging.INFO:
            return False

        if name == REQUEST_LOGGER_MODULE and isinstance(record.msg, str):
            if any(marker in record.msg for marker in _UNPATCHED_MARKERS):
                return False

        if record.exc_info or _has_exception_args(record):
            exc_type = None
            if record.exc_info and record.exc_info[0] is not None:
                exc_type = record.exc_info[0].__name__
            if record.args:
                args = record.args if isinstance(record.args, tuple) else (record.args,)
                record.args = tuple(
                    f"<{type(a).__name__}>" if isinstance(a, BaseException) else a for a in args
                )
            suffix = f" [{exc_type}; details suppressed]" if exc_type else " [details suppressed]"
            record.msg = f"{record.msg}{suffix}"
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _has_exception_args(record: logging.LogRecord) -> bool:
    args = record.args
    if not args:
        return False
    if not isinstance(args, tuple):
        args = (args,)
    return any(isinstance(a, BaseException) for a in args)
