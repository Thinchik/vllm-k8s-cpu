ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv ${VIRTUAL_ENV}

COPY requirements.txt /tmp/requirements.txt
# --require-hashes: pip поставит только те файлы, хеши которых есть в lock-файле
# --no-deps: никаких "догадок" резолвера, весь граф уже зафиксирован в requirements.txt
RUN pip install --require-hashes --no-deps -r /tmp/requirements.txt

# Наш патч логгирования ставится как обычный пакет + .pth-хук,
# который срабатывает при старте любого python-процесса внутри venv
COPY vllm_privacy/ ${VIRTUAL_ENV}/lib/python3.12/site-packages/vllm_privacy/
COPY vllm_privacy.pth ${VIRTUAL_ENV}/lib/python3.12/site-packages/vllm_privacy.pth

############################
# runtime
############################
FROM ${PYTHON_IMAGE} AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnuma1 \
        libtcmalloc-minimal4t64 \
        libgomp1 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid ${APP_GID} vllm \
    && useradd --system --uid ${APP_UID} --gid ${APP_GID} --home-dir /home/vllm --create-home --shell /usr/sbin/nologin vllm \
    && mkdir -p /models /cache /etc/vllm \
    && chown -R ${APP_UID}:${APP_GID} /models /cache

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY logging_config.json /etc/vllm/logging_config.json

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HOME=/home/vllm \
    PYTHONUNBUFFERED=1 \
    # все кэши в одном месте, чтобы можно было включить readOnlyRootFilesystem
    XDG_CACHE_HOME=/cache \
    VLLM_CACHE_ROOT=/cache/vllm \
    TORCHINDUCTOR_CACHE_DIR=/cache/torchinductor \
    TRITON_CACHE_DIR=/cache/triton \
    HF_HOME=/cache/hf \
    # веса читаем только с volume, в сеть не ходим
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    VLLM_NO_USAGE_STATS=1 \
    DO_NOT_TRACK=1 \
    # логгирование: свой конфиг с фильтром + запрет на логирование тел ответов
    VLLM_LOGGING_CONFIG_PATH=/etc/vllm/logging_config.json \
    VLLM_LOGGING_LEVEL=INFO \
    VLLM_DEBUG_LOG_API_SERVER_RESPONSE=0

USER ${APP_UID}:${APP_GID}
WORKDIR /home/vllm

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
