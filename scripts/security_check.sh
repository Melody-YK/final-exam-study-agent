#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:-}"

if [[ -n "$mode" && "$mode" != "--local-only" && "$mode" != "--with-advisories" ]]; then
  echo "usage: scripts/security_check.sh [--local-only|--with-advisories]" >&2
  exit 2
fi

uv run --project "$repo_root" python "$repo_root/scripts/check_private_data.py" --root "$repo_root"
uv run --project "$repo_root" python "$repo_root/scripts/dependency_check.py" --root "$repo_root"
uv run --project "$repo_root" pytest "$repo_root/tests/security" -q
uv run --project "$repo_root" pytest \
  "$repo_root/services/api/tests/unit/test_upload_validation.py" \
  "$repo_root/services/api/tests/unit/test_local_storage.py" \
  "$repo_root/services/api/tests/unit/test_errors_and_redaction.py" \
  "$repo_root/services/api/tests/unit/answering/test_citation_validator.py" \
  "$repo_root/services/api/tests/unit/answering/test_evidence_gate.py" \
  -q
uv run --project "$repo_root" ruff check \
  "$repo_root/scripts" "$repo_root/tests/security" "$repo_root/tests/evals"

if [[ "$mode" == "--local-only" ]]; then
  echo "partial: PostgreSQL security regressions were not run in local-only mode"
  echo "external-blocked: dependency vulnerability advisory databases were not queried"
  exit 0
fi

if [[ "$mode" == "--with-advisories" ]]; then
  uv run --project "$repo_root" python "$repo_root/scripts/run_advisory_audit.py"
fi

test_database_url="${TEST_DATABASE_URL:-postgresql+asyncpg:///study_agent_test}"
probe_database_url="${test_database_url/+asyncpg/}"
if ! TEST_DATABASE_PROBE_URL="$probe_database_url" \
  uv run --project "$repo_root" python -c '
import asyncio
import os

import asyncpg


async def probe() -> None:
    connection = await asyncpg.connect(os.environ["TEST_DATABASE_PROBE_URL"], timeout=2)
    try:
        await connection.execute("SELECT 1")
    finally:
        await connection.close()


asyncio.run(probe())
' >/dev/null 2>&1; then
  echo "external-blocked: PostgreSQL is unavailable for scope/lease/deletion security regressions"
  exit 77
fi

PYTHONPATH="$repo_root" TEST_DATABASE_URL="$test_database_url" \
  uv run --project "$repo_root" pytest \
  "$repo_root/services/api/tests/integration/test_course_repository.py" \
  "$repo_root/services/api/tests/integration/test_job_leases.py" \
  "$repo_root/services/api/tests/integration/test_full_deletion.py" \
  "$repo_root/services/api/tests/integration/test_queries.py" \
  -q
if [[ "$mode" != "--with-advisories" ]]; then
  echo "external-blocked: dependency vulnerability advisory databases were not queried"
fi
