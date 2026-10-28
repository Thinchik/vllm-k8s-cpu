from __future__ import annotations

import fcntl
import os
import shutil
import sys
import time
from pathlib import Path

MARKER = ".download-complete"
DEFAULT_PATTERNS = "*.json,*.safetensors,*.txt,*.model,*.tiktoken,*.jinja"


def log(msg: str) -> None:
    print(f"[model-downloader] {msg}", flush=True)


def main() -> int:
    model_id = os.environ.get("MODEL_ID", "").strip()
    if not model_id:
        log("MODEL_ID is not set")
        return 2
    revision = os.environ.get("MODEL_REVISION", "main").strip() or "main"
    models_dir = Path(os.environ.get("MODELS_DIR", "/models"))
    patterns = [p.strip() for p in os.environ.get("ALLOW_PATTERNS", DEFAULT_PATTERNS).split(",") if p.strip()]

    target = models_dir / model_id.replace("/", "--") / revision
    marker = target / MARKER

    if marker.exists():
        log(f"cache hit: {target}, skipping download")
        return 0

    models_dir.mkdir(parents=True, exist_ok=True)
    lock_path = models_dir / f".{model_id.replace('/', '--')}.lock"

    with open(lock_path, "w") as lock_file:
        log("waiting for lock")
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        # пока ждали блокировку, модель мог скачать соседний под
        if marker.exists():
            log(f"downloaded by another pod: {target}")
            return 0

        # импорт тут, чтобы при cache hit вообще не грузить huggingface_hub
        from huggingface_hub import snapshot_download

        token = os.environ.get("HF_TOKEN") or None
        tmp_dir = target.with_name(f"{revision}.partial")
        if tmp_dir.exists():
            log("removing leftovers of interrupted download")
            shutil.rmtree(tmp_dir)

        started = time.monotonic()
        log(f"downloading {model_id}@{revision} -> {target}")
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            local_dir=tmp_dir,
            allow_patterns=patterns,
            token=token,
        )
        # служебный кэш huggingface_hub внутри local_dir не нужен
        shutil.rmtree(tmp_dir / ".cache", ignore_errors=True)

        if target.exists():
            shutil.rmtree(target)
        tmp_dir.rename(target)
        marker.write_text(f"{model_id}@{revision}\n")
        log(f"done in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"download failed: {type(exc).__name__}: {exc}")
        sys.exit(1)
