"""Run local gates and write an atomic manifest without captured output or secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_SENSITIVE_ENV_PARTS = ("AUTH", "COOKIE", "CREDENTIAL", "KEY", "PASSWORD", "SECRET", "TOKEN")
_EXTERNAL_GATE_BLOCKERS = {
    "advisory-database": "dependency-advisory-database-not-queried",
    "browser-e2e": "browser-e2e-not-run",
    "libreoffice-live-baseline": "libreoffice-live-baseline-not-verified",
    "live-ocr": "live-ocr-model-not-run",
    "live-provider": "live-provider-not-run",
    "local-rc-health": "local-rc-not-run",
}


@dataclass(frozen=True, slots=True)
class GateSpec:
    name: str
    command: tuple[str, ...]
    required: bool
    timeout_seconds: int = 600


def run_gate_specs(
    root: Path,
    specs: Sequence[GateSpec],
    *,
    output: Path,
    source_environment: Mapping[str, str] | None = None,
    artifact_paths: Sequence[Path] = (),
) -> int:
    workspace = root.expanduser().resolve(strict=True)
    environment = _sanitized_environment(source_environment or os.environ)
    gates: list[dict[str, object]] = []
    required_failed = False
    for spec in specs:
        started = time.monotonic()
        try:
            result = subprocess.run(
                spec.command,
                cwd=workspace,
                env=environment,
                capture_output=True,
                check=False,
                timeout=spec.timeout_seconds,
            )
            exit_code = result.returncode
            captured = result.stdout + result.stderr
            status = (
                "passed" if exit_code == 0 else "external-blocked" if exit_code == 77 else "failed"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            exit_code = 124 if isinstance(exc, subprocess.TimeoutExpired) else 127
            captured = type(exc).__name__.encode("ascii")
            status = "failed"
        duration_ms = round((time.monotonic() - started) * 1000, 3)
        gates.append(
            {
                "name": spec.name,
                "required": spec.required,
                "status": status,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "output_bytes": len(captured),
                "output_sha256": hashlib.sha256(captured).hexdigest(),
            }
        )
        print(f"gate={spec.name} status={status} exit={exit_code}")
        if spec.required and status != "passed":
            required_failed = True

    artifacts = [_artifact_record(workspace, relative) for relative in artifact_paths]
    passed_gates = {item["name"] for item in gates if item["status"] == "passed"}
    external_blockers = [
        blocker for gate, blocker in _EXTERNAL_GATE_BLOCKERS.items() if gate not in passed_gates
    ]
    external_blockers.append("production-capacity-not-assessed")
    status = (
        "failed"
        if required_failed
        else "partial-external-blockers"
        if external_blockers or any(item["status"] == "external-blocked" for item in gates)
        else "passed"
    )
    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "local-rc-code-verifiable",
        "status": status,
        "production_readiness": "not-assessed",
        "contains_command_output": False,
        "contains_environment_values": False,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system().lower(),
        },
        "gates": gates,
        "artifacts": artifacts,
        "external_blockers": external_blockers,
    }
    destination = output.expanduser().absolute()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(destination)
    destination.chmod(0o600)
    return 1 if required_failed else 0


def default_gate_specs(*, quick: bool) -> tuple[GateSpec, ...]:
    python = "uv"
    base = (
        GateSpec(
            "private-data-secret-license",
            (python, "run", "python", "scripts/check_private_data.py", "--root", "."),
            True,
        ),
        GateSpec(
            "dependency-lock-integrity",
            (python, "run", "python", "scripts/dependency_check.py", "--root", "."),
            True,
        ),
        GateSpec(
            "security-regression",
            ("bash", "scripts/security_check.sh", "--local-only"),
            True,
        ),
        GateSpec(
            "evaluation-protocols",
            (python, "run", "pytest", "tests/evals", "-q"),
            True,
        ),
        GateSpec(
            "sbom",
            (
                python,
                "run",
                "python",
                "scripts/generate_sbom.py",
                "--output",
                ".local/evidence/sbom.cdx.json",
            ),
            True,
        ),
        GateSpec(
            "advisory-database",
            (python, "run", "python", "scripts/run_advisory_audit.py"),
            False,
        ),
        GateSpec(
            "live-provider",
            (python, "run", "python", "scripts/verify_external_evidence.py", "provider"),
            False,
        ),
        GateSpec(
            "live-ocr",
            (python, "run", "python", "scripts/verify_external_evidence.py", "ocr"),
            False,
        ),
        GateSpec(
            "libreoffice-live-baseline",
            (
                python,
                "run",
                "python",
                "scripts/verify_external_evidence.py",
                "libreoffice",
            ),
            False,
        ),
    )
    if quick:
        return (
            *base,
            GateSpec("local-rc-health", ("bash", "scripts/run_local_rc.sh", "--smoke"), False),
            GateSpec(
                "resource-preflight",
                (python, "run", "python", "scripts/run_resource_preflight.py"),
                False,
            ),
        )
    return (
        *base,
        GateSpec("format", (python, "run", "ruff", "format", "--check", "."), True),
        GateSpec("ruff", (python, "run", "ruff", "check", "."), True),
        GateSpec("typecheck", ("make", "typecheck"), True, 900),
        GateSpec("python-tests", ("make", "coverage"), True, 1200),
        GateSpec("web-tests", ("npm", "test"), True, 900),
        GateSpec("build", ("npm", "run", "build"), True, 900),
        GateSpec("browser-e2e", ("npm", "run", "test:e2e"), True, 1200),
        GateSpec("local-rc-health", ("bash", "scripts/run_local_rc.sh", "--smoke"), False),
        GateSpec(
            "resource-preflight",
            (python, "run", "python", "scripts/run_resource_preflight.py"),
            False,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/evidence/implementation-manifest.json"),
    )
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = (
        Path("evals/manifests/public.json"),
        Path("evals/fixtures/public/rag-seed-v1.jsonl"),
        Path("infra/compose/compose.yml"),
        Path("uv.lock"),
        Path("services/worker/profiles/paddle/uv.lock"),
        Path("services/worker/profiles/docling/uv.lock"),
        Path("package-lock.json"),
        Path(".local/evidence/sbom.cdx.json"),
        Path(".local/evidence/playwright/results.json"),
        Path(".local/evidence/advisory-audit.json"),
        Path(".local/evidence/provider-live-rag-smoke.json"),
        Path(".local/evidence/libreoffice-visual-baseline.json"),
        Path(".local/evidence/local-rc-smoke.json"),
        Path(".local/evidence/ocr-ingestion-smoke.json"),
        Path(".local/evals/ocr/live-benchmark.json"),
        Path(".local/evals/resource-preflight.json"),
    )
    return run_gate_specs(
        root,
        default_gate_specs(quick=arguments.quick),
        output=arguments.output,
        artifact_paths=artifacts,
    )


def _sanitized_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {
        key: value
        for key, value in source.items()
        if not any(part in key.upper() for part in _SENSITIVE_ENV_PARTS)
    }
    environment["PYTHONUNBUFFERED"] = "1"
    environment["RUN_LIVE_PROVIDER_TESTS"] = "0"
    environment["PROVIDER_CREDENTIALS_ROTATED"] = "0"
    return environment


def _artifact_record(root: Path, relative: Path) -> dict[str, object]:
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("evidence artifact paths must be workspace-relative")
    candidate = root / relative
    if candidate.is_symlink() or not candidate.is_file():
        return {"path": relative.as_posix(), "status": "missing"}
    payload = candidate.read_bytes()
    return {
        "path": relative.as_posix(),
        "status": "hashed",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
