#!/usr/bin/env bash
# Генерирует requirements.txt с полностью зафиксированным графом зависимостей
# (включая транзитивные) и sha256-хешами для обоих образов.
#
# Использует uv, если он установлен, иначе запускает его в контейнере.
# Целевая платформа задана явно, поэтому lock одинаковый на любой машине разработчика.
set -euo pipefail

cd "$(dirname "$0")/.."

PY=3.12
PLATFORM=x86_64-manylinux_2_39

compile() {
  local dir="$1"
  local args=(pip compile "${dir}/requirements.in"
    -o "${dir}/requirements.txt"
    --generate-hashes
    --emit-index-url
    --index-strategy unsafe-best-match
    --python-version "${PY}"
    --python-platform "${PLATFORM}"
    --no-header)
  if command -v uv >/dev/null 2>&1; then
    uv "${args[@]}"
  else
    docker run --rm -v "$PWD:/w" -w /w ghcr.io/astral-sh/uv:python3.12-trixie-slim uv "${args[@]}"
  fi
  echo "locked: ${dir}/requirements.txt ($(grep -c '==' "${dir}/requirements.txt") packages)"
}

compile docker/vllm
compile docker/model-downloader
