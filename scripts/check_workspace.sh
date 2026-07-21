#!/usr/bin/env bash
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"

if rg -q --hidden --glob '!**/.git/**' '\bsk-[A-Za-z0-9]{24,}\b' .; then
  echo "potential API credential found in workspace" >&2
  exit 1
fi

if git ls-files | rg -q '(^|/)(学校|操作系统|evals/private|secrets)(/|$)'; then
  echo "private course material or secrets are tracked" >&2
  exit 1
fi

uv run python scripts/check_private_data.py --root "$root"

docker compose -f infra/compose/compose.yml config --quiet
