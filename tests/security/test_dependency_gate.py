from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import verify_external_evidence
from scripts.dependency_check import check_dependency_locks

_PROFILE_LOCKS = (
    Path("services/worker/profiles/paddle/uv.lock"),
    Path("services/worker/profiles/docling/uv.lock"),
)


def _copy_python_locks(workspace_root: Path, destination: Path) -> None:
    shutil.copy(workspace_root / "uv.lock", destination / "uv.lock")
    for relative in _PROFILE_LOCKS:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(workspace_root / relative, target)


def test_repository_dependency_locks_have_local_integrity_metadata(
    workspace_root: Path,
) -> None:
    assert check_dependency_locks(workspace_root) == []


def test_dependency_gate_rejects_npm_package_without_integrity(
    workspace_root: Path,
    tmp_path: Path,
) -> None:
    _copy_python_locks(workspace_root, tmp_path)
    package_lock = json.loads((workspace_root / "package-lock.json").read_text(encoding="utf-8"))
    package_lock["packages"]["node_modules/react"].pop("integrity")
    (tmp_path / "package-lock.json").write_text(
        json.dumps(package_lock),
        encoding="utf-8",
    )

    findings = check_dependency_locks(tmp_path)

    assert any(item.code == "NPM_INTEGRITY_MISSING" for item in findings)


def test_advisory_verifier_rejects_environment_fingerprint_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paddle_profile = tmp_path / "services/worker/profiles/paddle"
    docling_profile = tmp_path / "services/worker/profiles/docling"
    for source, target in (
        ("uv.lock", tmp_path / "uv.lock"),
        ("services/worker/profiles/paddle/uv.lock", paddle_profile / "uv.lock"),
        ("services/worker/profiles/docling/uv.lock", docling_profile / "uv.lock"),
        ("package-lock.json", tmp_path / "package-lock.json"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(verify_external_evidence.__file__).parents[1] / source, target)
    evidence = tmp_path / ".local/evidence"
    evidence.mkdir(parents=True)
    evidence.chmod(0o700)

    def digest(path: Path) -> str:
        return verify_external_evidence.hashlib.sha256(path.read_bytes()).hexdigest()

    report = {
        "status": "passed",
        "query_mode": "live-advisory-databases",
        "sync_mode": "locked-before-audit",
        "lock_sha256": {
            "uv": digest(tmp_path / "uv.lock"),
            "paddle_uv": digest(paddle_profile / "uv.lock"),
            "docling_uv": digest(docling_profile / "uv.lock"),
            "npm": digest(tmp_path / "package-lock.json"),
        },
        "installed_environment_sha256": {
            "workspace": "stale",
            "paddle": "stale",
            "docling": "stale",
        },
        "vulnerability_counts": {
            "workspace_python": 0,
            "paddle_python": 0,
            "docling_python": 0,
            "npm_production": 0,
        },
    }
    receipt = evidence / "advisory-audit.json"
    receipt.write_text(json.dumps(report), encoding="utf-8")
    receipt.chmod(0o600)
    monkeypatch.setattr(verify_external_evidence, "_ROOT", tmp_path)
    monkeypatch.setattr(
        verify_external_evidence,
        "installed_environment_sha256",
        lambda environment: f"current:{environment}",
    )

    with pytest.raises(ValueError, match="stale or contains findings"):
        verify_external_evidence._verify_advisory()
