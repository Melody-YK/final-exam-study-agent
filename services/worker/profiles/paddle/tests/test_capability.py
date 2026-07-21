from __future__ import annotations

from pathlib import Path

import pytest
from study_worker_paddle import cli


def _create_backend_models(cache: Path, backend: str) -> None:
    for directory in cli._BACKEND_MODELS[backend].values():
        model = cache / directory
        model.mkdir(parents=True, exist_ok=True)
        for name in cli._REQUIRED_MODEL_FILES:
            (model / name).write_bytes(f"{backend}:{directory}:{name}".encode())


def test_capability_probe_is_fail_closed_without_packages_or_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _module: None)
    monkeypatch.setattr(cli.platform, "machine", lambda: "arm64")

    report = cli.capability_report(cache_root=tmp_path / "empty")

    assert report["ready"] is False
    assert report["reason_code"] == "OCR_PROFILE_NOT_INSTALLED"
    assert report["supports_ocr"] is False
    assert report["supports_mineru"] is False
    assert report["supports_paid_ocr"] is False


def test_capability_probe_requires_verified_packages_platform_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    _create_backend_models(cache, "general")
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _module: object())
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda name: f"test-{name}")
    monkeypatch.setattr(cli.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(cli.sys, "version_info", (3, 12, 0))
    cli._write_ready_marker(cache, "general")

    report = cli.capability_report(cache_root=cache)

    assert report["ready"] is True
    assert report["reason_code"] is None
    assert report["supports_ocr"] is True
    assert report["supports_pp_structure"] is False
    assert report["supports_mineru"] is False
    assert report["cached_file_count"] == len(cli._BACKEND_MODELS["general"]) * len(
        cli._REQUIRED_MODEL_FILES
    )


def test_capability_probe_rejects_legacy_marker_model_tamper_and_version_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    _create_backend_models(cache, "general")
    marker = cache / ".study-agent-general-ready"
    marker.write_text("ready\n", encoding="utf-8")
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _module: object())
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda name: f"test-{name}")
    monkeypatch.setattr(cli.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(cli.sys, "version_info", (3, 12, 0))

    assert cli.capability_report(cache_root=cache)["ready"] is False

    cli._write_ready_marker(cache, "general")
    model = cache / next(iter(cli._BACKEND_MODELS["general"].values())) / "inference.pdiparams"
    model.write_bytes(b"tampered")
    assert cli.capability_report(cache_root=cache)["ready"] is False

    _create_backend_models(cache, "general")
    cli._write_ready_marker(cache, "general")
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda name: f"changed-{name}")
    assert cli.capability_report(cache_root=cache)["ready"] is False


def test_capability_probe_requires_separate_pp_structure_warmup_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache = tmp_path / "models"
    cache.mkdir()
    _create_backend_models(cache, "general")
    _create_backend_models(cache, "pp-structure-v3")
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda _module: object())
    monkeypatch.setattr(cli.importlib.metadata, "version", lambda name: f"test-{name}")
    monkeypatch.setattr(cli.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(cli.sys, "version_info", (3, 12, 0))
    cli._write_ready_marker(cache, "general")
    cli._write_ready_marker(cache, "pp-structure-v3")

    report = cli.capability_report(cache_root=cache)

    assert report["ready"] is True
    assert report["supports_ocr"] is True
    assert report["supports_pp_structure"] is True
