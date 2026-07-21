#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/infra/compose/compose.yml"
mode="${1:---check}"

if [[ "$mode" != "--check" && "$mode" != "--smoke" ]]; then
  echo "usage: scripts/run_local_rc.sh [--check|--smoke]" >&2
  exit 2
fi

cd "$repo_root"
uv run python scripts/check_private_data.py --root "$repo_root"

docker_daemon_available() {
  uv run python -c '
import subprocess
import sys

try:
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
except (OSError, subprocess.TimeoutExpired):
    raise SystemExit(1) from None
raise SystemExit(0 if result.returncode == 0 else 1)
'
}

if ! command -v docker >/dev/null 2>&1; then
  echo "external-blocked: Docker CLI is unavailable; local RC was not started"
  exit 77
fi
docker compose -f "$compose_file" config --quiet
if ! docker_daemon_available; then
  echo "external-blocked: Docker daemon is unavailable; local RC was not started"
  exit 77
fi

rc_root="$repo_root/.local/rc"

check_running_rc() {
  if ! docker compose -f "$compose_file" ps --status running --services | rg -qx 'postgres'; then
    echo "external-blocked: PostgreSQL compose service is not running"
    return 77
  fi
  if ! docker compose -f "$compose_file" exec -T postgres \
    pg_isready -U study_agent -d study_agent >/dev/null 2>&1; then
    echo "external-blocked: PostgreSQL compose service is not healthy"
    return 77
  fi
  if ! curl --fail --silent --max-time 2 http://127.0.0.1:8000/healthz >/dev/null; then
    echo "external-blocked: local API health endpoint is unavailable"
    return 77
  fi
  if ! curl --fail --silent --max-time 2 http://127.0.0.1:5173/ >/dev/null; then
    echo "external-blocked: local Web endpoint is unavailable"
    return 77
  fi
  for process_name in index-runner worker; do
    pid_file="$rc_root/$process_name.pid"
    if [[ ! -f "$pid_file" ]] || ! kill -0 "$(<"$pid_file")" 2>/dev/null; then
      echo "external-blocked: local $process_name process is unavailable"
      return 77
    fi
  done
  echo "local RC health check passed"
}

if [[ "$mode" == "--check" ]]; then
  check_running_rc
  exit $?
fi

mkdir -p "$rc_root"
chmod 700 "$rc_root"
uv sync --all-packages --locked
docker compose -f "$compose_file" up -d --wait postgres
uv run python -c \
  'import asyncio; from study_agent.infrastructure.db.migrations import upgrade_database; asyncio.run(upgrade_database("postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent"))'
for port in 8000 5173; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "local RC port $port is already occupied" >&2
    exit 1
  fi
done

spawned_pids=()
cleanup() {
  for pid in "${spawned_pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  rm -f "$rc_root/api.pid" "$rc_root/index-runner.pid" "$rc_root/worker.pid" "$rc_root/web.pid"
}
trap cleanup EXIT INT TERM

export WORKER_TOKEN="${WORKER_TOKEN:-local-rc-process-token-2026-rotate-me}"
export WORKER_API_BASE_URL="http://127.0.0.1:8000"
export WORKER_MODE="local"
if [[ -x "$repo_root/services/worker/profiles/paddle/.venv/bin/study-agent-paddle-profile" \
  && -d "$repo_root/.local/models/paddlex" ]]; then
  export WORKER_PADDLE_PROFILE_BIN="$repo_root/services/worker/profiles/paddle/.venv/bin/study-agent-paddle-profile"
  export WORKER_PADDLE_MODEL_CACHE="$repo_root/.local/models/paddlex"
fi
if [[ -x "/Applications/LibreOffice.app/Contents/MacOS/soffice" ]]; then
  export WORKER_SOFFICE_BIN="/Applications/LibreOffice.app/Contents/MacOS/soffice"
fi

runtime_command() {
  local entrypoint="$1"
  shift
  if [[ -n "${COVERAGE_PROCESS_START:-}" ]]; then
    exec env -u COVERAGE_PROCESS_START "$repo_root/.venv/bin/coverage" run --parallel-mode \
      "$entrypoint" "$@"
  else
    exec "$entrypoint" "$@"
  fi
}

runtime_command "$repo_root/.venv/bin/study-agent-api" >"$rc_root/api.log" 2>&1 &
api_pid=$!
spawned_pids+=("$api_pid")
echo "$api_pid" >"$rc_root/api.pid"

runtime_command "$repo_root/.venv/bin/study-agent-index-runner" \
  >"$rc_root/index-runner.log" 2>&1 &
runner_pid=$!
spawned_pids+=("$runner_pid")
echo "$runner_pid" >"$rc_root/index-runner.pid"

runtime_command "$repo_root/.venv/bin/study-agent-worker" run >"$rc_root/worker.log" 2>&1 &
worker_pid=$!
spawned_pids+=("$worker_pid")
echo "$worker_pid" >"$rc_root/worker.pid"

"$repo_root/node_modules/.bin/vite" "$repo_root/apps/web" --host 127.0.0.1 \
  >"$rc_root/web.log" 2>&1 &
web_pid=$!
spawned_pids+=("$web_pid")
echo "$web_pid" >"$rc_root/web.pid"

for _attempt in $(seq 1 120); do
  if curl --fail --silent --max-time 1 http://127.0.0.1:8000/healthz >/dev/null \
    && curl --fail --silent --max-time 1 http://127.0.0.1:5173/ >/dev/null; then
    break
  fi
  for pid in "$api_pid" "$runner_pid" "$worker_pid" "$web_pid"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "local RC process exited during startup" >&2
      exit 1
    fi
  done
  sleep 1
done

check_running_rc
uv run python scripts/run_live_ocr_ingestion_smoke.py
uv run python -m evals.rag.run_benchmark --mode test-double
uv run python -m evals.rag.run_benchmark --mode no-provider
uv run python scripts/collect_resource_observations.py \
  --api-pid "$api_pid" \
  --runner-pid "$runner_pid"
uv run python scripts/run_resource_preflight.py
uv run python scripts/record_local_rc_smoke.py
echo "local RC, live General OCR ingestion, and local resource preflight completed"
