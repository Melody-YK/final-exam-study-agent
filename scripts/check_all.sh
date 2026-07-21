#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

arguments=()
if [[ "${CHECK_ALL_QUICK:-0}" == "1" ]]; then
  arguments+=(--quick)
fi

if ((${#arguments[@]})); then
  exec uv run python scripts/generate_implementation_manifest.py "${arguments[@]}" "$@"
fi
exec uv run python scripts/generate_implementation_manifest.py "$@"
