# Paddle OCR Profile

This profile is intentionally outside the default API and Worker workspace. It pins the
Apple Silicon OCR runtime without adding Paddle, PaddleOCR, PaddleX, or model files to the
lightweight Worker installation.

Create the isolated environment only on the local M1 Worker:

```bash
uv sync --project services/worker/profiles/paddle
uv run --project services/worker/profiles/paddle study-agent-paddle-profile warmup \
  --backend general --cache-root /absolute/private/model/cache
uv run --project services/worker/profiles/paddle study-agent-paddle-profile capabilities \
  --cache-root /absolute/private/model/cache
```

`warmup` is an operator-only installation action. It initializes the requested backend, runs one
self-authored page through the vendor engine and strict adapter, and writes a private readiness
marker only after that execution succeeds. The base Worker allowlist does not permit `warmup`.

The capability command never downloads or initializes a model. It returns a non-zero exit
status until the pinned packages are installed and a separately managed model cache exists.
`supports_mineru` and `supports_paid_ocr` remain false by design.

Configure the base Worker with the absolute profile executable and model cache paths. On startup,
the Worker runs the capability probe in a private sandbox and advertises `ocr-v1` only when both
the probe and the bounded subprocess handler are available:

```bash
export WORKER_PADDLE_PROFILE_BIN=/absolute/path/to/study-agent-paddle-profile
export WORKER_PADDLE_MODEL_CACHE=/absolute/path/to/model-cache
study-agent-worker ocr-capabilities
study-agent-worker run
```

The base Worker never imports Paddle. Each claimed page is executed with a fixed `run` argv,
bounded output and timeout, hash checks, and a private result file. General OCR is the default;
PP-StructureV3 is used only when `WORKER_COMPLEX_PARSER_ENABLED=true`, the profile reports the
capability, and the General result triggers the local complexity heuristic. MinerU and paid OCR
remain disabled.
