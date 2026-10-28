# vLLM на CPU в Kubernetes

Инференс `Qwen/Qwen2.5-1.5B-Instruct` через vLLM 0.32.0 (CPU, bf16) в Minikube или в обычном k8s-кластере.

Что сделано:

| Требование | Где |
|---|---|
| Rootless-образ vLLM | `docker/vllm/Dockerfile` (UID 10001, `USER` в образе + `runAsNonRoot` в поде) |
| Зафиксированные зависимости, включая транзитивные | `docker/*/requirements.txt` с sha256-хешами, установка с `--require-hashes --no-deps` |
| Логи без промптов и ответов | `docker/vllm/vllm_privacy/` + `docker/vllm/logging_config.json` |
| Кэширование весов на PVC | `k8s/base/pvc.yaml`, `docker/model-downloader/download_model.py` |
| Секреты (HF-токен, Bearer-токен API) | `secretGenerator` в `k8s/overlays/minikube`, `secretKeyRef` в Deployment |
| Параметры vLLM без пересборки | ConfigMap `vllm-config` (`config.yaml` -> `vllm serve --config`) и `vllm-runtime` (env) |
| Бонус 1: kustomize | `k8s/base` + `k8s/overlays/minikube`, `kubectl apply -k` |
| Бонус 2: initContainer | отдельный легкий rootless-образ `docker/model-downloader` |
| Бонус 3: нагрузочный тест | `loadtest/loadtest.py`, результаты и выводы ниже |

## Структура

```
docker/
  vllm/                  образ vLLM: Dockerfile, entrypoint, патч логгирования, lock-файл
  model-downloader/      образ initContainer: скрипт загрузки весов, lock-файл
k8s/
  base/                  Namespace, PVC, Deployment, Service, генераторы ConfigMap
  overlays/minikube/     секреты и теги образов для локального запуска
  plain/                 те же манифесты без kustomize (kubectl apply -f)
scripts/lock-requirements.sh
loadtest/loadtest.py
tests/test_privacy_logging.py
```

## Как это работает

```
Pod vllm
 ├─ initContainer model-downloader (python:3.12-slim + huggingface_hub)
 │    /models/Qwen--Qwen2.5-1.5B-Instruct/main/.download-complete есть?
 │      да  -> выход, сеть не нужна
 │      нет -> snapshot_download во временный каталог -> rename -> маркер
 └─ container vllm (rootless)
      HF_HUB_OFFLINE=1, веса читаются только с PVC (read-only mount)
      vllm serve /models/... --config /etc/vllm/config/config.yaml
      VLLM_API_KEY из Secret -> все пути /v1/* требуют Bearer-токен, /health открыт для проб
```

Загрузка вынесена в initContainer, поэтому основному контейнеру не нужен HF-токен и доступ в интернет. Модель скачивается во временный каталог и переименовывается только после полной загрузки. Если под упадет на середине, недокачанные веса не будут приняты за готовые. Файловая блокировка на PVC защищает от двух одновременных загрузок.

### Логгирование

По умолчанию vLLM пишет тексты запросов в двух местах: `RequestLogger.log_inputs` (prompt на DEBUG, полный `SamplingParams` на INFO) и `RequestLogger.log_outputs` (сгенерированный текст на INFO). Плюс некоторые эндпоинты на DEBUG логируют тело запроса целиком.

Защита сделана в два слоя:

1. **Патч `RequestLogger`** (`vllm_privacy/request_logger_patch.py`). Методы подменяются import-хуком, который подключается через `vllm_privacy.pth` в site-packages. Так патч применяется в каждом Python-процессе vLLM, в том числе в дополнительных API-серверах при `--api-server-count > 1`. Исходники vLLM при этом не трогаются, и обновление версии не требует перебазирования патча.
2. **Фильтр на handler-е** (`vllm_privacy/logging_filter.py`, подключается через `VLLM_LOGGING_CONFIG_PATH`). Отбрасывает DEBUG-записи `vllm.entrypoints.*`, записи `RequestLogger` в исходном формате (на случай, если патч не применился) и убирает текст и traceback исключений из эндпоинтов: в ошибках валидации pydantic бывают фрагменты входных данных.

Дополнительно в образе зафиксированы `VLLM_LOGGING_LEVEL=INFO` и `VLLM_DEBUG_LOG_API_SERVER_RESPONSE=0`.

Формат строк в логе после патча (значения для примера):

```
INFO ... Request chatcmpl-8f3c... received: prompt_tokens=27, has_embeds=False, params=[type=SamplingParams n=1 max_tokens=64 temperature=0.7 top_p=0.8 top_k=20], lora=None
INFO ... Response chatcmpl-8f3c... finished: output_tokens=41, finish_reason=stop, streaming=False
INFO:     10.244.0.1:51234 - "POST /v1/chat/completions HTTP/1.1" 200 OK
```

`SamplingParams` целиком не выводится: в нем могут быть stop-строки, `logit_bias` и JSON-схемы structured output. Проверка без установки vLLM: `make test`.

## Запуск в Minikube

Нужно: Minikube, kubectl, Docker с BuildKit, CPU x86_64 с AVX2 (лучше AVX512). Модели 1.5B в bf16 нужно около 3 ГиБ под веса и 4 ГиБ под KV-кэш.

```bash
# 0. Кластер
minikube start --cpus 6 --memory 12g --disk-size 40g

# 1. Lock-файлы зависимостей (один раз; результат коммитится в репозиторий)
make lock

# 2. Образы собираются сразу в Minikube, registry не нужен
make images
#   альтернатива: eval $(minikube -p minikube docker-env) && DOCKER_BUILDKIT=1 docker build -t vllm-cpu:local docker/vllm

# 3. Секреты: HF-токен (для Qwen2.5 можно оставить пустым) и случайный API-токен
make secrets
#   при необходимости вписать HF_TOKEN в k8s/overlays/minikube/secrets/hf-token.env

# 4. Деплой
make deploy        # kubectl apply -k k8s/overlays/minikube
make wait          # первый старт дольше: скачиваются веса

# 5. Проверка
make port-forward  # в отдельном терминале
make smoke         # ответ модели + 401 без токена
make logs          # в логах есть факт запроса, но нет текста
```

Проверка кэша: `make restart`, затем `make logs`. initContainer пишет `cache hit ... skipping download` и завершается за секунды.

Смена параметров инференса: правим `k8s/base/config/config.yaml` или `k8s/base/config/runtime.env` и снова делаем `make deploy`. У ConfigMap меняется хеш в имени, kustomize обновляет ссылку в Deployment, и под перезапускается сам.

Без kustomize: секреты создаются командами, остальное через `kubectl apply -f k8s/plain/`:

```bash
kubectl apply -f k8s/plain/00-namespace.yaml
kubectl -n llm-inference create secret generic hf-token --from-literal=HF_TOKEN="$HF_TOKEN"
kubectl -n llm-inference create secret generic vllm-api-token --from-literal=VLLM_API_KEY="$(openssl rand -hex 32)"
kubectl apply -f k8s/plain/
```

Удаление: `make clean`. Вместе с остальными ресурсами удаляется и PVC со скачанными весами.

### Безопасность пода

- `runAsNonRoot`, UID/GID 10001, `allowPrivilegeEscalation: false`, `capabilities: drop ALL`, `seccompProfile: RuntimeDefault`;
- `readOnlyRootFilesystem: true`, запись только в `emptyDir` (`/cache`, `/tmp`, `/dev/shm`) и на PVC (только initContainer, у vLLM том смонтирован read-only);
- namespace с Pod Security Admission `restricted`;
- `automountServiceAccountToken: false`: поду не нужен доступ к API Kubernetes;
- HF-токен есть только у initContainer, API-токен только у vLLM;
- `trust-remote-code: false`.

Если какая-то версия vLLM или torch попытается писать за пределы `/cache` и `/tmp`, под упадет с `Read-only file system`. Путь нужно добавить в `emptyDir` или временно выключить `readOnlyRootFilesystem`.

### Почему так выставлены CPU-параметры

`VLLM_CPU_OMP_THREADS_BIND=auto` привязывает потоки к ядрам по NUMA-нодам, но внутри контейнера с CPU-лимитом видит все ядра ноды. В итоге потоков больше, чем выделено квоты, и CFS начинает троттлить под. Поэтому привязка выключена (`nobind`), а `OMP_NUM_THREADS` равен лимиту минус одно ядро: оно остается API-серверу, как советует документация vLLM для CPU. `requests` равны `limits`, чтобы под получил класс QoS `Guaranteed`.

## Нагрузочное тестирование

```bash
make port-forward   # в отдельном терминале
make loadtest       # или:
python loadtest/loadtest.py --url http://localhost:8000 --model qwen2.5-1.5b-instruct \
    --prompts short,long --concurrency 1,4,8 --requests 16 --max-tokens 128
```

Скрипт шлет стриминговые запросы в `/v1/chat/completions` и считает TTFT по первому чанку с текстом, Latency по полному ответу, TPOT и Throughput (токенов/с и запросов/с по всему прогону). Чтобы сценарии были сравнимы, длина ответа фиксирована (`ignore_eos`, 128 токенов). В начало каждого промпта добавляется случайная строка, иначе prefix caching превращает тест длинных промптов в замер кэша. Перед каждым сценарием идет один прогревочный запрос. Результаты сохраняются в `loadtest/results/` (JSON и Markdown).

Можно сверить со встроенным бенчмарком: `vllm bench serve --backend openai-chat --endpoint /v1/chat/completions ...`.

### Результаты

Стенд: `<CPU, число ядер, AVX512 да/нет>`, Minikube `--cpus 6 --memory 12g`, лимиты пода 4 CPU / 10 GiB, конфигурация из `k8s/base/config`.

| Промпт | Параллелизм | Вход, ток. | TTFT p50, с | TTFT p95, с | Latency p50, с | Latency p95, с | TPOT p50, мс | Throughput, ток/с | Throughput, req/s |
|---|---|---|---|---|---|---|---|---|---|
| short | 1 | | | | | | | | |
| short | 4 | | | | | | | | |
| short | 8 | | | | | | | | |
| long | 1 | | | | | | | | |
| long | 4 | | | | | | | | |
| long | 8 | | | | | | | | |

### Какие параметры менять для роста Throughput

**А) Много запросов с небольшим числом токенов.** Prefill у таких запросов короткий, почти все время уходит на decode, а decode на CPU упирается в пропускную способность памяти: на каждом шаге читаются все веса модели. Значит, выгоднее обрабатывать за шаг как можно больше последовательностей.

- `max-num-seqs`: поднять с 16 до 64-128, чтобы в batch decode попадало больше запросов. Пока ядра не загружены полностью, суммарный throughput растет почти линейно, TPOT отдельного запроса при этом увеличивается.
- `VLLM_CPU_KVCACHE_SPACE`: увеличить. Без запаса KV-кэша большой батч не соберется, планировщик начнет вытеснять запросы (preemption).
- `max-model-len`: уменьшить до реального максимума (например, 2048). Меньше памяти резервируется под одну последовательность, больше запросов помещается одновременно.
- `max-num-batched-tokens`: держать умеренным (2048-4096). Короткие prefill и так проходят за шаг, большее значение мало что дает.
- `enable-prefix-caching`: оставить включенным, если у запросов общий системный промпт. Тогда его prefill не пересчитывается.
- Масштабирование вширь: на CPU несколько реплик по 4-8 ядер часто дают больше суммарного throughput, чем одна реплика на все ядра. Плюс HPA по числу запросов в очереди (`vllm:num_requests_waiting` из `/metrics`).
- Если в фронтенде упирается токенизация или HTTP, то `api-server-count` > 1.

**Б) Запросов немного, но контекст огромный.** Узкое место смещается в prefill. Это вычислительная нагрузка (большие матричные умножения), и KV-кэш одного запроса занимает много памяти.

- `enable-chunked-prefill` (уже включен) и `max-num-batched-tokens`: поднять до 8192-16384. Prefill идет более крупными кусками, GEMM на CPU работают эффективнее, TTFT падает. Цена: decode соседних запросов на время такого шага задерживается.
- `max-model-len`: выставить под реальный размер контекста, а `VLLM_CPU_KVCACHE_SPACE` увеличить так, чтобы несколько длинных запросов помещались без вытеснения. Preemption длинного запроса означает пересчет всего его prefill, это самая дорогая ситуация.
- `max-num-seqs`: наоборот, снизить (4-8). Меньше конкуренции за KV-кэш, меньше вытеснений.
- `enable-prefix-caching`: дает большой выигрыш, если длинный контекст повторяется (многоходовый диалог, RAG по одним и тем же документам).
- `kv-cache-dtype` с fp8 (если CPU-бэкенд этой версии его поддерживает) и квантизованные веса уменьшают объем памяти на токен и на модель. Влияние на качество нужно проверить отдельно.
- На многосокетном сервере: `tensor-parallel-size` по числу NUMA-нод, чтобы каждый ранг работал со своей локальной памятью.

## Что можно улучшить

- Закрепить базовые образы по digest и подписывать собранные образы (cosign).
- Добавить NetworkPolicy: к vLLM пускать только из namespace клиентов, egress закрыть (initContainer-у нужен только huggingface.co).
- Хранить секреты во внешнем хранилище (External Secrets / Vault) вместо файлов в overlay.
- Собирать метрики `/metrics` в Prometheus и строить по ним HPA.
