"""Import-хук: патчит модуль RequestLogger сразу после его загрузки.

Импортируется из ``vllm_privacy.pth`` при старте интерпретатора. Сам по себе
ничего тяжелого не импортирует (vllm и torch не трогает), поэтому на
процессы, которые не используют vLLM, не влияет.
"""

import importlib.abc
import importlib.machinery
import sys

from vllm_privacy import REQUEST_LOGGER_MODULE


class _PatchingLoader(importlib.abc.Loader):
    def __init__(self, wrapped):
        self._wrapped = wrapped

    def create_module(self, spec):
        return self._wrapped.create_module(spec)

    def exec_module(self, module):
        self._wrapped.exec_module(module)
        from vllm_privacy.request_logger_patch import patch_module

        patch_module(module)

    def __getattr__(self, item):
        # get_source, get_filename и пр. отдаем оригинальному загрузчику
        return getattr(self._wrapped, item)


class _PrivacyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != REQUEST_LOGGER_MODULE:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return spec
        spec.loader = _PatchingLoader(spec.loader)
        return spec


def install() -> None:
    if any(isinstance(f, _PrivacyFinder) for f in sys.meta_path):
        return
    sys.meta_path.insert(0, _PrivacyFinder())
    # если модуль уже был импортирован до хука - патчим сразу
    module = sys.modules.get(REQUEST_LOGGER_MODULE)
    if module is not None:
        from vllm_privacy.request_logger_patch import patch_module

        patch_module(module)


try:
    install()
except Exception as exc:  # хук не должен ронять интерпретатор
    print(f"[vllm_privacy] failed to install import hook: {exc!r}", file=sys.stderr)
