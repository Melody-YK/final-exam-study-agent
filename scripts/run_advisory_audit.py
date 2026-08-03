"""Query live Python/npm advisory databases and write lock-bound local evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.environment_fingerprint import (  # noqa: E402
    installed_environment_sha256,
    site_packages,
)

_EVIDENCE = _ROOT / ".local" / "evidence"
_SENSITIVE_ENV_PARTS = ("AUTH", "COOKIE", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN")


def _safe_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not any(part in name.upper() for part in _SENSITIVE_ENV_PARTS)
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_private(path: Path, payload: object) -> None:
    if path.is_symlink():
        raise ValueError("advisory evidence output must not be a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _pip_audit(label: str, environment: Path) -> tuple[dict[str, Any], int]:
    temporary = _EVIDENCE / f"pip-audit-{label}.raw.tmp.json"
    result = subprocess.run(
        [
            "uvx",
            "pip-audit",
            "--path",
            str(site_packages(environment)),
            "--format",
            "json",
            "--output",
            str(temporary),
        ],
        cwd=_ROOT,
        env=_safe_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=300,
    )
    try:
        payload = json.loads(temporary.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError(f"{label} Python advisory database query failed") from None
    output = _EVIDENCE / f"pip-audit-{label}.json"
    _write_private(output, payload)
    temporary.unlink(missing_ok=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("dependencies"), list):
        raise RuntimeError(f"{label} Python advisory report is invalid")
    return payload, result.returncode


def _npm_audit() -> tuple[dict[str, Any], int]:
    result = subprocess.run(
        [
            "npm",
            "audit",
            "--registry=https://registry.npmjs.org",
            "--audit-level=high",
            "--omit=dev",
            "--json",
        ],
        cwd=_ROOT,
        env=_safe_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=300,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("npm advisory database query failed") from None
    if not isinstance(payload, dict) or "error" in payload:
        raise RuntimeError("npm advisory database query failed")
    _write_private(_EVIDENCE / "npm-audit-production.json", payload)
    return payload, result.returncode


def _pip_vulnerability_count(payload: dict[str, Any]) -> int:
    return sum(
        len(dependency.get("vulns", []))
        for dependency in payload["dependencies"]
        if isinstance(dependency, dict) and isinstance(dependency.get("vulns", []), list)
    )


def _locked_sync() -> None:
    commands = (
        ("uv", "sync", "--all-packages", "--locked"),
        (
            "uv",
            "sync",
            "--project",
            "services/worker/profiles/paddle",
            "--locked",
        ),
        (
            "uv",
            "sync",
            "--project",
            "services/worker/profiles/docling",
            "--locked",
        ),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=_ROOT,
            env=_safe_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError("locked Python environment sync failed")


def main() -> int:
    _EVIDENCE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(_EVIDENCE, 0o700)
    try:
        _locked_sync()
        environment_sha256 = {
            "workspace": installed_environment_sha256(_ROOT / ".venv"),
            "paddle": installed_environment_sha256(
                _ROOT / "services" / "worker" / "profiles" / "paddle" / ".venv"
            ),
            "docling": installed_environment_sha256(
                _ROOT / "services" / "worker" / "profiles" / "docling" / ".venv"
            ),
        }
        workspace, workspace_exit = _pip_audit("workspace", _ROOT / ".venv")
        paddle, paddle_exit = _pip_audit(
            "paddle", _ROOT / "services" / "worker" / "profiles" / "paddle" / ".venv"
        )
        docling, docling_exit = _pip_audit(
            "docling",
            _ROOT / "services" / "worker" / "profiles" / "docling" / ".venv",
        )
        npm, npm_exit = _npm_audit()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"external-blocked: {exc}", file=sys.stderr)
        return 77
    workspace_vulnerabilities = _pip_vulnerability_count(workspace)
    paddle_vulnerabilities = _pip_vulnerability_count(paddle)
    docling_vulnerabilities = _pip_vulnerability_count(docling)
    npm_vulnerabilities = npm.get("metadata", {}).get("vulnerabilities", {})
    npm_total = (
        int(npm_vulnerabilities.get("total", 0)) if isinstance(npm_vulnerabilities, dict) else -1
    )
    passed = (
        workspace_exit == 0
        and paddle_exit == 0
        and docling_exit == 0
        and npm_exit == 0
        and workspace_vulnerabilities == 0
        and paddle_vulnerabilities == 0
        and docling_vulnerabilities == 0
        and npm_total == 0
    )
    summary = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "query_mode": "live-advisory-databases",
        "sync_mode": "locked-before-audit",
        "lock_sha256": {
            "uv": _sha256(_ROOT / "uv.lock"),
            "paddle_uv": _sha256(_ROOT / "services" / "worker" / "profiles" / "paddle" / "uv.lock"),
            "docling_uv": _sha256(
                _ROOT / "services" / "worker" / "profiles" / "docling" / "uv.lock"
            ),
            "npm": _sha256(_ROOT / "package-lock.json"),
        },
        "vulnerability_counts": {
            "workspace_python": workspace_vulnerabilities,
            "paddle_python": paddle_vulnerabilities,
            "docling_python": docling_vulnerabilities,
            "npm_production": npm_total,
        },
        "installed_environment_sha256": environment_sha256,
        "contains_environment_values": False,
        "contains_command_output": False,
        "production_readiness": "not-assessed",
    }
    _write_private(_EVIDENCE / "advisory-audit.json", summary)
    print(f"advisory_audit_status={summary['status']}")
    print(f"workspace_python_vulnerabilities={workspace_vulnerabilities}")
    print(f"paddle_python_vulnerabilities={paddle_vulnerabilities}")
    print(f"docling_python_vulnerabilities={docling_vulnerabilities}")
    print(f"npm_production_vulnerabilities={npm_total}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
