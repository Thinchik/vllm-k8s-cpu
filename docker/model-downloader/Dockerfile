# syntax=docker/dockerfile:1.7
#
# Легковесный rootless-образ для initContainer: только python + huggingface_hub.
# Итоговый размер порядка 150 МБ против нескольких ГБ у образа vLLM.

ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.txt

FROM ${PYTHON_IMAGE}
ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --system --gid ${APP_GID} app \
    && useradd --system --uid ${APP_UID} --gid ${APP_GID} --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /models \
    && chown ${APP_UID}:${APP_GID} /models

COPY --from=builder /opt/venv /opt/venv
COPY --chmod=0755 download_model.py /usr/local/bin/download_model.py

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    HF_HOME=/tmp/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    MODELS_DIR=/models

USER ${APP_UID}:${APP_GID}
ENTRYPOINT ["python", "/usr/local/bin/download_model.py"]
