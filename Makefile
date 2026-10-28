NS       ?= llm-inference
OVERLAY  ?= k8s/overlays/minikube
SECRETS  := $(OVERLAY)/secrets

.PHONY: lock images secrets deploy wait port-forward smoke logs restart loadtest test clean

## Зафиксировать все Python-зависимости с хешами (нужно один раз и после смены версий)
lock:
	./scripts/lock-requirements.sh

## Собрать оба образа сразу внутри Minikube (без registry)
images:
	minikube image build -t vllm-cpu:local docker/vllm
	minikube image build -t vllm-model-downloader:local docker/model-downloader

## Создать локальные файлы секретов (если их еще нет), API-токен генерируется случайно
secrets:
	@test -f $(SECRETS)/hf-token.env || cp $(SECRETS)/hf-token.env.example $(SECRETS)/hf-token.env
	@test -f $(SECRETS)/api-token.env || echo "VLLM_API_KEY=$$(openssl rand -hex 32)" > $(SECRETS)/api-token.env
	@echo "secrets: $(SECRETS)/*.env (в git не попадают)"

deploy: secrets
	kubectl apply -k $(OVERLAY)

wait:
	kubectl -n $(NS) rollout status deployment/vllm --timeout=20m

port-forward:
	kubectl -n $(NS) port-forward svc/vllm 8000:8000

## Тестовый запрос (port-forward должен быть запущен в соседнем терминале)
smoke:
	@. $(SECRETS)/api-token.env && curl -sS http://localhost:8000/v1/chat/completions \
	  -H "Authorization: Bearer $$VLLM_API_KEY" -H "Content-Type: application/json" \
	  -d '{"model":"qwen2.5-1.5b-instruct","messages":[{"role":"user","content":"Привет! Ответь одним предложением."}],"max_tokens":64}'
	@echo
	@echo "без токена (ожидается 401):"
	@curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/v1/models

logs:
	kubectl -n $(NS) logs deployment/vllm -c model-downloader
	kubectl -n $(NS) logs deployment/vllm -c vllm --tail=50

restart:
	kubectl -n $(NS) rollout restart deployment/vllm

loadtest:
	@. $(SECRETS)/api-token.env && VLLM_API_KEY=$$VLLM_API_KEY python loadtest/loadtest.py

test:
	python -m pytest tests -q

clean:
	kubectl delete -k $(OVERLAY) --ignore-not-found
