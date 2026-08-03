from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _profile_cli(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = Path(__file__).parents[1] / "profiles" / "docling" / "src"
    monkeypatch.syspath_prepend(str(source))
    sys.modules.pop("study_worker_docling.cli", None)
    return importlib.import_module("study_worker_docling.cli")


def test_docling_runtime_forces_model_caches_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = _profile_cli(monkeypatch)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    for name in (
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
    ):
        monkeypatch.delenv(name, raising=False)

    cli._configure_cache_environment(artifacts, offline=True)

    cache = artifacts / "huggingface"
    assert os.environ["HF_HOME"] == str(cache)
    assert os.environ["HUGGINGFACE_HUB_CACHE"] == str(cache / "hub")
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_docling_warmup_does_not_force_offline_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cli = _profile_cli(monkeypatch)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    cli._configure_cache_environment(artifacts, offline=False)

    assert "HF_HUB_OFFLINE" not in os.environ
    assert "TRANSFORMERS_OFFLINE" not in os.environ
