#!/usr/bin/env bash
# Точка входа vLLM-контейнера.
# Веса уже должен был положить initContainer, здесь только проверяем и запускаем сервер.
set -euo pipefail

: "${MODEL_ID:?MODEL_ID is not set (ConfigMap vllm-runtime)}"
MODEL_REVISION="${MODEL_REVISION:-main}"
MODELS_DIR="${MODELS_DIR:-/models}"
VLLM_CONFIG_FILE="${VLLM_CONFIG_FILE:-/etc/vllm/config/config.yaml}"

# Та же схема путей, что и в download_model.py
MODEL_PATH="${MODELS_DIR}/${MODEL_ID//\//--}/${MODEL_REVISION}"

if [[ ! -f "${MODEL_PATH}/.download-complete" ]]; then
  echo "[entrypoint] model not found in ${MODEL_PATH}; initContainer must download it first" >&2
  exit 1
fi

if [[ -z "${VLLM_API_KEY:-}" ]]; then
  echo "[entrypoint] VLLM_API_KEY is empty; refusing to start an unauthenticated endpoint" >&2
  exit 1
fi

# LD_PRELOAD по рекомендации vLLM CPU (tcmalloc + Intel OpenMP), только если библиотеки есть
preload=()
for lib in /usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4 "${VIRTUAL_ENV:-/opt/venv}/lib/libiomp5.so"; do
  [[ -f "$lib" ]] && preload+=("$lib")
done
if (( ${#preload[@]} )); then
  export LD_PRELOAD="$(IFS=:; echo "${preload[*]}")${LD_PRELOAD:+:$LD_PRELOAD}"
fi

mkdir -p "${VLLM_CACHE_ROOT:-/cache/vllm}" "${TORCHINDUCTOR_CACHE_DIR:-/cache/torchinductor}" "${TRITON_CACHE_DIR:-/cache/triton}"

echo "[entrypoint] serving ${MODEL_ID}@${MODEL_REVISION} from ${MODEL_PATH}"

# Параметры инференса берутся из ConfigMap (config.yaml), CLI-аргументы контейнера идут последними
# и имеют наивысший приоритет. Токен API передается через env VLLM_API_KEY (Secret),
# чтобы он не светился в списке процессов.
exec vllm serve "${MODEL_PATH}" --config "${VLLM_CONFIG_FILE}" "$@"
