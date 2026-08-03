# Docling isolated profile

This optional environment supplies the `standard` and `vlm` fallbacks used by
the default PDF parsing chain. The base Worker never imports Docling and never
downloads models during startup.

```bash
uv sync --project services/worker/profiles/docling
mkdir -p .local/models/docling
uv run --project services/worker/profiles/docling study-agent-docling-profile warmup \
  --backend standard --artifacts-root "$PWD/.local/models/docling"
uv run --project services/worker/profiles/docling study-agent-docling-profile warmup \
  --backend vlm --artifacts-root "$PWD/.local/models/docling"
```

`standard` must be warmed before `vlm`. Warmup may download model artifacts and
must be run explicitly. Configure the base Worker with:

```bash
WORKER_DOCLING_PROFILE_BIN="$PWD/services/worker/profiles/docling/.venv/bin/study-agent-docling-profile"
WORKER_DOCLING_ARTIFACTS_ROOT="$PWD/.local/models/docling"
```

The Worker uses the fast native result when it passes quality checks. It calls
Docling standard only for weak or structurally unresolved pages, then calls the
VLM backend only if the standard result still leaves unresolved content.
