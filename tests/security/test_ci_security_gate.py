from __future__ import annotations

import shlex
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def _check_job(workspace_root: Path) -> dict[str, object]:
    workflow_path = workspace_root / ".github/workflows/ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert isinstance(workflow, dict)
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    job = jobs.get("check")
    assert isinstance(job, dict)
    return job


def test_ci_check_job_provides_healthy_pgvector_database(workspace_root: Path) -> None:
    job = _check_job(workspace_root)
    services = job.get("services")
    assert isinstance(services, dict)
    postgres = services.get("postgres")
    assert isinstance(postgres, dict)

    assert postgres.get("image") == "pgvector/pgvector:pg16"
    service_env = postgres.get("env")
    assert isinstance(service_env, dict)
    assert service_env.get("POSTGRES_DB") == "study_agent_test"
    assert service_env.get("POSTGRES_USER") == "study_agent"
    assert service_env.get("POSTGRES_HOST_AUTH_METHOD") == "trust"
    assert "5432:5432" in {str(port) for port in postgres.get("ports", [])}

    options = str(postgres.get("options", ""))
    assert "pg_isready -U study_agent -d study_agent_test" in options
    assert "--health-interval 5s" in options
    assert "--health-timeout 3s" in options
    assert "--health-retries 10" in options

    job_env = job.get("env")
    assert isinstance(job_env, dict)
    database_url = job_env.get("TEST_DATABASE_URL")
    assert isinstance(database_url, str)
    parsed_url = urlsplit(database_url)
    assert parsed_url.scheme == "postgresql+asyncpg"
    assert parsed_url.hostname == "127.0.0.1"
    assert parsed_url.port == 5432
    assert parsed_url.username == "study_agent"
    assert parsed_url.path == "/study_agent_test"


def test_ci_check_job_runs_full_security_and_advisory_gates(workspace_root: Path) -> None:
    job = _check_job(workspace_root)
    steps = job.get("steps")
    assert isinstance(steps, list)
    commands = [
        step["run"] for step in steps if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]

    assert "make check" in commands
    security_commands = [command for command in commands if "security_check.sh" in command]
    assert len(security_commands) == 1
    assert "--local-only" not in shlex.split(security_commands[0])
    assert "npm audit --registry=https://registry.npmjs.org --audit-level=critical" in commands
    assert (
        "npm audit --registry=https://registry.npmjs.org --audit-level=low --omit=dev" in commands
    )
    assert "uv run python scripts/run_advisory_audit.py" in commands
