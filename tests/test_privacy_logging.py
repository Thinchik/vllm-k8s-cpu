"""Проверка патча логгирования без установки vLLM.

Создаем заглушку модуля vllm.entrypoints.serve.utils.request_logger с тем же
поведением, что у оригинала (пишет prompt и output), и убеждаемся, что после
подключения import-хука и фильтра секретный текст в лог не попадает,
а факт запроса и ответа логируется.

Запуск:  python -m pytest tests/ -q   (или просто python tests/test_privacy_logging.py)
"""

from __future__ import annotations

import io
import logging
import sys
import textwrap
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker" / "vllm"))

SECRET_PROMPT = "мой пароль от почты qwerty123"
SECRET_OUTPUT = "ваш пароль qwerty123 сохранен"

STUB = textwrap.dedent(
    '''
    import logging
    logger = logging.getLogger("vllm.entrypoints.serve.utils.request_logger")

    class RequestLogger:
        def __init__(self, *, max_log_len=None):
            self.max_log_len = max_log_len

        def log_inputs(self, request_id, prompt, prompt_token_ids, prompt_embeds, params, lora_request):
            logger.debug("Request %s details: prompt: %r, prompt_token_ids: %s", request_id, prompt, prompt_token_ids)
            logger.info("Received request %s: params: %s, lora_request: %s.", request_id, params, lora_request)

        def log_request_body(self, request):
            logger.debug("Request %s JSON body: %s", "x", request)

        def log_outputs(self, request_id, outputs, output_token_ids, finish_reason=None, is_streaming=False, delta=False):
            logger.info("Generated response %s: output: %r, finish_reason: %s", request_id, outputs, finish_reason)
    '''
)


def _make_stub_package(tmp_path: Path) -> None:
    pkg = tmp_path / "vllm" / "entrypoints" / "serve" / "utils"
    pkg.mkdir(parents=True)
    for d in [tmp_path / "vllm", *(p for p in pkg.parents if str(p).startswith(str(tmp_path / "vllm"))), pkg]:
        (d / "__init__.py").touch()
    (pkg / "request_logger.py").write_text(STUB)
    sys.path.insert(0, str(tmp_path))


class _Params:
    n = 1
    max_tokens = 64
    temperature = 0.2
    top_p = 0.9
    top_k = None
    stop = [SECRET_PROMPT]  # не должно попасть в лог


def _capture() -> io.StringIO:
    from vllm_privacy.logging_filter import PrivacyFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root = logging.getLogger("vllm")
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)  # даже на DEBUG текст не должен утечь
    root.propagate = False
    return stream


def test_prompts_and_outputs_are_not_logged(tmp_path: Path) -> None:
    _make_stub_package(tmp_path)
    stream = _capture()

    import vllm_privacy._bootstrap  # noqa: F401  (то же, что делает .pth)
    from vllm.entrypoints.serve.utils.request_logger import RequestLogger

    assert getattr(RequestLogger, "_vllm_privacy_patched", False)

    rl = RequestLogger(max_log_len=None)
    rl.log_inputs("req-1", SECRET_PROMPT, [1, 2, 3, 4], None, _Params(), None)
    rl.log_request_body({"messages": [{"content": SECRET_PROMPT}]})
    rl.log_outputs("req-1", SECRET_OUTPUT, [5, 6], finish_reason="stop", is_streaming=True, delta=True)
    rl.log_outputs("req-1", SECRET_OUTPUT, [5, 6, 7], finish_reason="stop", is_streaming=True, delta=False)

    log = stream.getvalue()
    assert "qwerty123" not in log
    assert "Request req-1 received: prompt_tokens=4" in log
    assert "max_tokens=64" in log
    assert "Response req-1 finished: output_tokens=3, finish_reason=stop" in log
    # на дельты стрима отдельных записей нет
    assert log.count("Response req-1") == 1


def test_filter_blocks_unpatched_and_debug_records() -> None:
    stream = _capture()
    other = logging.getLogger("vllm.entrypoints.anthropic.serving")
    other.debug("Received messages request %s", SECRET_PROMPT)
    raw = logging.getLogger("vllm.entrypoints.serve.utils.request_logger")
    raw.info("Generated response %s: output: %r, finish_reason: %s", "r", SECRET_OUTPUT, "stop")
    assert "qwerty123" not in stream.getvalue()


def test_filter_strips_exception_details() -> None:
    stream = _capture()
    err_logger = logging.getLogger("vllm.entrypoints.anthropic.api_router")
    try:
        raise ValueError(f"input_value='{SECRET_PROMPT}'")
    except ValueError as e:
        err_logger.exception("Error in create_messages: %s", e)
    log = stream.getvalue()
    assert "qwerty123" not in log
    assert "ValueError" in log


def test_engine_logs_untouched() -> None:
    stream = _capture()
    logging.getLogger("vllm.v1.engine").info("KV cache size: %s tokens", 1000)
    assert "KV cache size: 1000 tokens" in stream.getvalue()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_prompts_and_outputs_are_not_logged(Path(d))
    test_filter_blocks_unpatched_and_debug_records()
    test_filter_strips_exception_details()
    test_engine_logs_untouched()
    print("all privacy logging tests passed")
