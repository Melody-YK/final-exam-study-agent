#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUN_LIVE_PROVIDER_TESTS:-}" != "1" || "${PROVIDER_CREDENTIALS_ROTATED:-}" != "1" ]]; then
  echo "external-blocked: set both live-provider safety gates after credential rotation" >&2
  exit 2
fi

if [[ -z "${EMBEDDING_API_KEY:-}" || -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "external-blocked: inject both rotated provider secrets through the runtime environment" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uv run --project "$repo_root" pytest -m live \
  "$repo_root/services/api/tests/live/test_provider_contracts.py" "$@"
