from __future__ import annotations

import json
from pathlib import Path

import pytest

from study_worker.capabilities import probe_docling_profile
from study_worker.sandbox import SandboxManager


def _script(path: Path, payload: dict[str, object], *, exit_code: int) -> Path:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{encoded}'\nexit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path.absolute()


@pytest.mark.asyncio
async def test_docling_probe_tracks_standard_and_vlm_readiness_separately(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    report = {
        "schema_version": "1.0",
        "profile": "docling-v1",
        "standard_ready": True,
        "standard_reason_code": None,
        "vlm_ready": False,
        "vlm_reason_code": "DOCLING_VLM_NOT_WARMED",
        "versions": {"docling": "2.117.0"},
        "artifacts_root": str(artifacts.resolve()),
    }
    executable = _script(tmp_path / "docling-profile", report, exit_code=0)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        status = await probe_docling_profile(
            executable=executable,
            artifacts_root=artifacts.absolute(),
            sandbox=sandbox,
        )

    assert status.standard_ready is True
    assert status.vlm_ready is False
    assert status.vlm_reason_code == "DOCLING_VLM_NOT_WARMED"
    assert dict(status.versions) == {"docling": "2.117.0"}


@pytest.mark.asyncio
async def test_docling_probe_rejects_an_unaligned_artifacts_root(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    report = {
        "schema_version": "1.0",
        "profile": "docling-v1",
        "standard_ready": True,
        "standard_reason_code": None,
        "vlm_ready": True,
        "vlm_reason_code": None,
        "versions": {"docling": "2.117.0"},
        "artifacts_root": str(tmp_path / "different"),
    }
    executable = _script(tmp_path / "docling-profile", report, exit_code=0)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        status = await probe_docling_profile(
            executable=executable,
            artifacts_root=artifacts.absolute(),
            sandbox=sandbox,
        )

    assert status.standard_ready is False
    assert status.standard_reason_code == "DOCLING_PROFILE_REPORT_INVALID"
