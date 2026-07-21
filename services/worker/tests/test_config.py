from pathlib import Path

import pytest
from pydantic import ValidationError

from study_worker.config import WorkerMode, WorkerSettings


def test_local_worker_fails_closed_without_token() -> None:
    with pytest.raises(ValidationError, match="worker token is required"):
        WorkerSettings(_env_file=None, mode=WorkerMode.LOCAL)


def test_test_worker_allows_no_token_and_resolves_paths(tmp_path: Path) -> None:
    settings = WorkerSettings(
        _env_file=None,
        mode=WorkerMode.TEST,
        work_root=tmp_path / "worker",
        soffice_bin="~/bin/soffice",
        paddle_profile_bin=tmp_path / "paddle-profile",
        paddle_model_cache=tmp_path / "paddle-cache",
    )

    assert settings.token is None
    assert settings.work_root.is_absolute()
    assert settings.local_storage_root.is_absolute()
    assert settings.soffice_bin is not None
    assert settings.soffice_bin.is_absolute()
    assert settings.paddle_profile_bin is not None
    assert settings.paddle_profile_bin.is_absolute()
    assert settings.paddle_model_cache is not None
    assert settings.paddle_model_cache.is_absolute()
    assert settings.complex_parser_enabled is False


def test_local_worker_rejects_non_loopback_api() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.LOCAL,
            token="worker-secret",
            api_base_url="https://example.com",
        )


def test_production_worker_requires_https() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.PRODUCTION,
            token="worker-secret",
            api_base_url="http://worker.example.com",
        )


def test_production_worker_rejects_weak_token() -> None:
    with pytest.raises(ValidationError, match="worker token"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.PRODUCTION,
            token="x",
            api_base_url="https://worker.example.com",
        )


def test_production_worker_accepts_strong_token() -> None:
    settings = WorkerSettings(
        _env_file=None,
        mode=WorkerMode.PRODUCTION,
        token="correct-horse-battery-staple-8ca7",
        api_base_url="https://worker.example.com",
    )

    assert settings.token is not None


def test_worker_api_url_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError, match="must not include credentials"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.TEST,
            api_base_url="http://worker:secret@127.0.0.1:8000",
        )


def test_long_poll_timeout_must_leave_transport_headroom() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.TEST,
            poll_wait_seconds=30,
            request_timeout_seconds=30,
        )


def test_poll_backoff_bounds_are_ordered() -> None:
    with pytest.raises(ValidationError, match="maximum poll backoff"):
        WorkerSettings(
            _env_file=None,
            mode=WorkerMode.TEST,
            poll_backoff_initial_seconds=10,
            poll_backoff_max_seconds=1,
        )
