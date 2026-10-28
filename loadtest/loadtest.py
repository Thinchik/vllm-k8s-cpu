#!/usr/bin/env python3
"""Нагрузочный тест OpenAI-совместимого эндпоинта vLLM.

Для каждого сценария (размер промпта x уровень параллелизма) отправляет
N стриминговых запросов /v1/chat/completions и считает:
  * TTFT     - время до первого токена ответа, с
  * Latency  - полное время ответа, с
  * TPOT     - среднее время на один выходной токен после первого, мс
  * Throughput - выходных токенов в секунду и запросов в секунду по всему прогону

Пример:
  export VLLM_API_KEY=...
  python loadtest/loadtest.py --url http://localhost:8000 \
      --model qwen2.5-1.5b-instruct --concurrency 1,4,8 --requests 16
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import string
import time
from dataclasses import dataclass, field

import httpx

# Нейтральный текст для набивки длинного промпта. Каждому запросу добавляется
# случайный префикс, чтобы prefix caching не превращал тест в замер кэша.
FILLER = (
    "Отчет о работе сервиса за квартал включает показатели доступности, "
    "время отклика, число обращений в поддержку и итоги релизов. "
)

PROMPT_SIZES = {
    # примерно 30 и 1500 входных токенов для Qwen2.5
    "short": 1,
    "long": 40,
}


@dataclass
class Result:
    ok: bool
    ttft: float = 0.0
    latency: float = 0.0
    output_tokens: int = 0
    prompt_tokens: int = 0
    error: str = ""


@dataclass
class Scenario:
    prompt_size: str
    concurrency: int
    results: list[Result] = field(default_factory=list)
    wall_time: float = 0.0


def build_prompt(size: str) -> str:
    nonce = "".join(random.choices(string.ascii_lowercase, k=12))
    body = FILLER * PROMPT_SIZES[size]
    return f"[{nonce}] {body}\nКратко перескажи текст выше в трех предложениях."


async def one_request(client: httpx.AsyncClient, args, size: str) -> Result:
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": build_prompt(size)}],
        "max_tokens": args.max_tokens,
        "temperature": 0.0,
        "stream": True,
        "stream_options": {"include_usage": True},
        # фиксированная длина ответа делает сценарии сравнимыми
        "ignore_eos": True,
    }
    started = time.perf_counter()
    first_token_at = None
    usage = {}
    try:
        async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                return Result(ok=False, error=f"HTTP {resp.status_code}: {body[:200]!r}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if first_token_at is None and choices and choices[0].get("delta", {}).get("content"):
                    first_token_at = time.perf_counter()
    except httpx.HTTPError as exc:
        return Result(ok=False, error=f"{type(exc).__name__}: {exc}")
    finished = time.perf_counter()
    return Result(
        ok=True,
        ttft=(first_token_at or finished) - started,
        latency=finished - started,
        output_tokens=int(usage.get("completion_tokens", 0)),
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
    )


async def run_scenario(args, size: str, concurrency: int) -> Scenario:
    scenario = Scenario(prompt_size=size, concurrency=concurrency)
    sem = asyncio.Semaphore(concurrency)
    headers = {"Authorization": f"Bearer {args.api_key}"}
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=timeout, limits=limits) as client:

        async def guarded():
            async with sem:
                return await one_request(client, args, size)

        # прогрев: один запрос, в статистику не идет
        await one_request(client, args, size)
        started = time.perf_counter()
        scenario.results = await asyncio.gather(*(guarded() for _ in range(args.requests)))
        scenario.wall_time = time.perf_counter() - started
    return scenario


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(q / 100 * (len(values) - 1))))
    return values[idx]


def summarize(s: Scenario) -> dict:
    ok = [r for r in s.results if r.ok]
    ttft = [r.ttft for r in ok]
    lat = [r.latency for r in ok]
    tpot = [
        (r.latency - r.ttft) / (r.output_tokens - 1) * 1000 for r in ok if r.output_tokens > 1
    ]
    out_tokens = sum(r.output_tokens for r in ok)
    return {
        "prompt": s.prompt_size,
        "concurrency": s.concurrency,
        "requests_ok": len(ok),
        "requests_failed": len(s.results) - len(ok),
        "avg_prompt_tokens": round(statistics.mean(r.prompt_tokens for r in ok)) if ok else 0,
        "ttft_p50_s": pct(ttft, 50),
        "ttft_p95_s": pct(ttft, 95),
        "latency_p50_s": pct(lat, 50),
        "latency_p95_s": pct(lat, 95),
        "tpot_p50_ms": pct(tpot, 50),
        "throughput_tok_s": out_tokens / s.wall_time if s.wall_time else 0.0,
        "throughput_req_s": len(ok) / s.wall_time if s.wall_time else 0.0,
        "errors": sorted({r.error for r in s.results if not r.ok})[:3],
    }


def to_markdown(rows: list[dict]) -> str:
    head = (
        "| Промпт | Параллелизм | Вход, ток. | TTFT p50, с | TTFT p95, с | Latency p50, с | "
        "Latency p95, с | TPOT p50, мс | Throughput, ток/с | Throughput, req/s | Ошибки |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    lines = [
        f"| {r['prompt']} | {r['concurrency']} | {r['avg_prompt_tokens']} | {r['ttft_p50_s']:.2f} | "
        f"{r['ttft_p95_s']:.2f} | {r['latency_p50_s']:.2f} | {r['latency_p95_s']:.2f} | "
        f"{r['tpot_p50_ms']:.0f} | {r['throughput_tok_s']:.1f} | {r['throughput_req_s']:.2f} | "
        f"{r['requests_failed']} |"
        for r in rows
    ]
    return head + "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", ""))
    parser.add_argument("--model", default="qwen2.5-1.5b-instruct")
    parser.add_argument("--prompts", default="short,long", help="через запятую: short,long")
    parser.add_argument("--concurrency", default="1,4,8")
    parser.add_argument("--requests", type=int, default=16, help="запросов на сценарий")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", default="loadtest/results")
    args = parser.parse_args()

    if not args.api_key:
        parser.error("нужен --api-key или переменная VLLM_API_KEY")

    rows = []
    for size in args.prompts.split(","):
        for conc in (int(c) for c in args.concurrency.split(",")):
            print(f"-> prompt={size} concurrency={conc} requests={args.requests}", flush=True)
            rows.append(summarize(await run_scenario(args, size, conc)))
            print(to_markdown(rows[-1:]), flush=True)

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(args.out, f"results-{stamp}.json"), "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    table = to_markdown(rows)
    with open(os.path.join(args.out, f"results-{stamp}.md"), "w") as f:
        f.write(table)
    print("\n" + table)


if __name__ == "__main__":
    asyncio.run(main())
