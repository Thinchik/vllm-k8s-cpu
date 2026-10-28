"""Privacy-патч логгирования vLLM.

Два уровня защиты:

1. ``request_logger_patch`` - подменяет методы ``RequestLogger`` так, что в лог
   попадают только метаданные запроса (id, число токенов, базовые параметры
   семплирования, finish_reason). Тексты промптов и ответов не пишутся.
   Патч применяется import-хуком из ``_bootstrap`` (подключается через .pth),
   поэтому работает в любом python-процессе vLLM, включая дополнительные
   API-серверы при ``--api-server-count > 1``.

2. ``logging_filter.PrivacyFilter`` - фильтр на handler-е (подключается через
   ``VLLM_LOGGING_CONFIG_PATH``). Страхует на случай, если патч по какой-то
   причине не применился или текст запроса попал в лог другим путем
   (DEBUG-логи эндпоинтов, исключения валидации и т.п.).
"""

REQUEST_LOGGER_MODULE = "vllm.entrypoints.serve.utils.request_logger"
