from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from study_worker.capabilities import probe_paddle_profile
from study_worker.sandbox import SandboxManager


def _probe_script(path: Path, payload: dict[str, object], *, exit_code: int = 0) -> Path:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{encoded}'\nexit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o700)
    return path.absolute()


def _ready_report(cache: Path) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "profile": "paddle-ocr-v1",
        "ready": True,
        "reason_code": None,
        "platform": "arm64",
        "python": "3.12.10",
        "versions": {
            "paddleocr": "3.7.0",
            "paddlepaddle": "3.3.1",
            "paddlex": "3.7.0",
        },
        "missing_packages": [],
        "cache_root": str(cache.resolve()),
        "cached_file_count": 1,
        "supports_ocr": True,
        "supports_pp_structure": True,
        "supports_mineru": False,
        "supports_paid_ocr": False,
    }


@pytest.mark.asyncio
async def test_base_probe_requires_executable_and_nonempty_model_cache(tmp_path: Path) -> None:
    manager = SandboxManager(tmp_path / "sandboxes")
    with manager.create() as sandbox:
        missing_executable = await probe_paddle_profile(
            executable=None,
            model_cache=None,
            sandbox=sandbox,
        )
        empty_cache = tmp_path / "empty-cache"
        empty_cache.mkdir()
        unavailable_cache = await probe_paddle_profile(
            executable=Path("/does/not/exist"),
            model_cache=empty_cache.absolute(),
            sandbox=sandbox,
        )

    assert missing_executable.reason_code == "OCR_PROFILE_NOT_CONFIGURED"
    assert missing_executable.ready is False
    assert unavailable_cache.reason_code == "OCR_PROFILE_UNAVAILABLE"
    assert unavailable_cache.supports_ocr is False


@pytest.mark.asyncio
async def test_base_probe_accepts_only_aligned_fail_closed_profile_report(tmp_path: Path) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"self-authored-model-marker")
    executable = _probe_script(tmp_path / "profile", _ready_report(cache))
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        status = await probe_paddle_profile(
            executable=executable,
            model_cache=cache.absolute(),
            sandbox=sandbox,
        )

    assert status.ready is True
    assert status.supports_ocr is True
    assert status.supports_pp_structure is True
    assert dict(status.versions)["paddleocr"] == "3.7.0"
    assert not any(name == "paddle" or name.startswith("paddle.") for name in sys.modules)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ({"supports_paid_ocr": True}, "OCR_PROFILE_REPORT_INVALID"),
        ({"supports_mineru": True}, "OCR_PROFILE_REPORT_INVALID"),
        ({"cache_root": "/different/cache"}, "OCR_PROFILE_REPORT_INVALID"),
        ({"cached_file_count": 0}, "OCR_PROFILE_REPORT_INVALID"),
        ({"versions": {"paddleocr": "recognized raw text"}}, "OCR_PROFILE_REPORT_INVALID"),
    ],
)
async def test_base_probe_rejects_privileged_or_inconsistent_reports(
    tmp_path: Path,
    mutation: dict[str, object],
    reason_code: str,
) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"self-authored-model-marker")
    report = {**_ready_report(cache), **mutation}
    executable = _probe_script(tmp_path / "profile", report)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        status = await probe_paddle_profile(
            executable=executable,
            model_cache=cache.absolute(),
            sandbox=sandbox,
        )

    assert status.ready is False
    assert status.reason_code == reason_code
    assert status.supports_ocr is False


@pytest.mark.asyncio
async def test_base_probe_rejects_symlinked_executable_and_cache(tmp_path: Path) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"self-authored-model-marker")
    executable = _probe_script(tmp_path / "profile", _ready_report(cache))
    executable_link = tmp_path / "profile-link"
    executable_link.symlink_to(executable)
    cache_link = tmp_path / "cache-link"
    cache_link.symlink_to(cache, target_is_directory=True)
    manager = SandboxManager(tmp_path / "sandboxes")

    with manager.create() as sandbox:
        symlinked_executable = await probe_paddle_profile(
            executable=executable_link.absolute(),
            model_cache=cache.absolute(),
            sandbox=sandbox,
        )
        symlinked_cache = await probe_paddle_profile(
            executable=executable,
            model_cache=cache_link.absolute(),
            sandbox=sandbox,
        )

    assert symlinked_executable.reason_code == "OCR_PROFILE_UNAVAILABLE"
    assert symlinked_cache.reason_code == "OCR_MODELS_NOT_CACHED"
    assert os.access(executable, os.X_OK)
