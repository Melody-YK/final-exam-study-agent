# Evaluation Reports

Generated evaluation reports belong under ignored local roots such as `.local/evals/` or
`evals/reports/generated/`. Reports written inside the repository are rejected outside those
roots. Report files are created with mode `0600` and must not contain credentials, source text,
questions, answers, or private course material.

## OCR

`evals.ocr.run_benchmark` consumes an absolute OCR manifest plus normalized observation files
from an external directory or an ignored local directory such as `.local/evals/ocr/results`.

Observation files may contain recognized text and therefore must never be committed. Generated
reports are restricted to ignored repository paths by default and contain only input hashes,
parser/dependency versions, aggregate quality metrics, duration, peak RSS, cache status, and
failed page ordinals. They deliberately set `contains_raw_text=false`. `live_ocr_verified`
becomes `true` only when every observation explicitly records `execution_mode=live-model`;
test-double and mixed reports remain `false`. This proves model execution provenance, not a
production quality SLA or LibreOffice quality.

Example:

```bash
python -m evals.ocr.run_benchmark \
  --manifest /absolute/path/to/ocr-manifest.json \
  --observations-root /absolute/path/to/ocr-observations \
  --output evals/reports/generated/ocr-benchmark.json
```

## RAG, Citations, and Refusals

`evals.rag.run_benchmark` generates separate reports for each Provider boundary:

- `test-double` evaluates the public seed's precomputed Dense/BM25 rankings, reciprocal-rank
  fusion, citation bookkeeping, and refusal protocol. It is not application E2E or live-model
  quality evidence.
- `no-provider` verifies explicit abstention when no Provider is available. It does not claim
  answer quality.
- `live-provider` accepts only local observation files and requires both
  `RUN_LIVE_PROVIDER_TESTS=1` and `PROVIDER_CREDENTIALS_ROTATED=1`. The runner never makes the
  external call itself.

The reports contain only the public seed hash, hashed case keys, identifier counts, aggregate
retrieval/citation/refusal metrics, latency values, and machine-readable failure codes. They set
`contains_raw_text=false` and `production_readiness=not-assessed`.

Local protocol runs:

```bash
uv run python -m evals.rag.run_benchmark --mode test-double
uv run python -m evals.rag.run_benchmark --mode no-provider
uv run python -m evals.rag.ablation \
  evals/fixtures/public/rag-seed-v1.jsonl \
  --output .local/evals/rag/ablation.json
```

Both commands write beneath `.local/evals/rag/` by default. A live report additionally requires
an explicit local observation file:

```bash
RUN_LIVE_PROVIDER_TESTS=1 PROVIDER_CREDENTIALS_ROTATED=1 \
  uv run python -m evals.rag.run_benchmark \
  --mode live-provider \
  --observations /absolute/path/to/redacted-observations.json
```

## Resource Preflight

`scripts/run_resource_preflight.py` aggregates locally collected observations recorded under an
exact 2 GiB-equivalent memory limit. Missing observations return exit code `77`. Every generated
report keeps `local_equivalent_only=true`, `production_capacity_verified=false`, and
`production_readiness=not-assessed`; local measurements cannot establish server or production
capacity.
