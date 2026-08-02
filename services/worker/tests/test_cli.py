import json
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

import study_worker.cli as worker_cli
from study_worker.capabilities import OcrCapabilityStatus
from study_worker.cli import app
from study_worker.config import WorkerMode, WorkerSettings
from study_worker.dispatcher import TaskHandler, TaskResult
from study_worker.poller.poller import IdleWorker, StoppableService, WorkerEventLogger

runner = CliRunner()


def _valid_local_env(tmp_path: Path) -> dict[str, str]:
    return {
        "WORKER_MODE": "local",
        "WORKER_API_BASE_URL": "http://127.0.0.1:8000",
        "WORKER_TOKEN": "must-not-be-printed",
        "WORKER_WORK_ROOT": str(tmp_path / "worker"),
        "WORKER_PADDLE_PROFILE_BIN": "",
        "WORKER_PADDLE_MODEL_CACHE": "",
    }


def test_entrypoint_help_imports_without_loading_runtime_settings() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "config-check" in result.output
    assert "run" in result.output


def test_config_check_fails_closed_without_disclosing_validation_input() -> None:
    result = runner.invoke(app, ["config-check"], env={"WORKER_TOKEN": ""})

    assert result.exit_code == 2
    assert "配置无效" in result.output


def test_config_check_reports_only_non_secret_settings(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config-check"], env=_valid_local_env(tmp_path))

    assert result.exit_code == 0
    assert "配置有效" in result.output
    assert "must-not-be-printed" not in result.output


def test_ocr_capability_command_fails_closed_and_never_enables_claims(tmp_path: Path) -> None:
    result = runner.invoke(app, ["ocr-capabilities"], env=_valid_local_env(tmp_path))

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["ready"] is False
    assert payload["reason_code"] == "OCR_PROFILE_NOT_CONFIGURED"
    assert payload["supports_ocr"] is False
    assert payload["claim_enabled"] is False
    assert "must-not-be-printed" not in result.output


def test_ocr_capability_command_enables_claim_only_with_verified_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = OcrCapabilityStatus(
        ready=True,
        reason_code=None,
        supports_ocr=True,
        supports_pp_structure=True,
        cached_file_count=1,
    )

    async def prepared(
        settings: WorkerSettings,
        sandboxes: object,
    ) -> tuple[OcrCapabilityStatus, TaskHandler]:
        del settings, sandboxes

        async def handler(*_args: object) -> TaskResult:
            raise AssertionError("capability command must not execute OCR")

        return ready, handler

    monkeypatch.setattr(worker_cli, "_prepare_ocr_runtime", prepared)

    result = runner.invoke(app, ["ocr-capabilities"], env=_valid_local_env(tmp_path))

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ready"] is True
    assert payload["supports_pp_structure"] is True
    assert payload["claim_enabled"] is True


def test_run_starts_zero_capability_idle_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_worker(settings: WorkerSettings, parse_handler: TaskHandler | None) -> None:
        captured["settings"] = settings
        captured["handler"] = parse_handler

    monkeypatch.setattr(worker_cli, "_native_task_handler", lambda settings: None)
    monkeypatch.setattr(worker_cli, "run_worker", fake_run_worker)
    result = runner.invoke(app, ["run"], env=_valid_local_env(tmp_path))

    assert result.exit_code == 0
    assert "capabilities=0" in result.output
    assert "claims=disabled" in result.output
    assert "must-not-be-printed" not in result.output
    assert captured["handler"] is None


def test_invalid_handler_plugin_degrades_to_idle_without_exposing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[TaskHandler | None] = []

    def invalid_handler(settings: WorkerSettings) -> TaskHandler:
        del settings
        raise worker_cli.NativeHandlerUnavailable("sensitive plugin details")

    async def fake_run_worker(settings: WorkerSettings, parse_handler: TaskHandler | None) -> None:
        del settings
        captured.append(parse_handler)

    monkeypatch.setattr(worker_cli, "_native_task_handler", invalid_handler)
    monkeypatch.setattr(worker_cli, "run_worker", fake_run_worker)

    result = runner.invoke(app, ["run"], env=_valid_local_env(tmp_path))

    assert result.exit_code == 0
    assert "安全降级" in result.output
    assert "sensitive plugin details" not in result.output
    assert captured == [None]


def test_installed_native_handler_entry_point_builds_from_validated_settings(
    tmp_path: Path,
) -> None:
    settings = WorkerSettings(
        _env_file=None,
        mode=WorkerMode.TEST,
        work_root=tmp_path / "worker",
        local_storage_root=tmp_path / "storage",
    )

    handler = worker_cli._native_task_handler(settings)

    assert callable(handler)


@pytest.mark.asyncio
async def test_zero_capability_runtime_never_constructs_control_plane_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="study_worker.events")
    settings = WorkerSettings(
        _env_file=None,
        mode=WorkerMode.TEST,
        work_root=tmp_path / "worker",
        local_storage_root=tmp_path / "storage",
    )
    observed: list[StoppableService] = []

    def unexpected_client(**kwargs: object) -> None:
        del kwargs
        raise AssertionError("zero-capability runtime must not construct a client")

    async def stop_idle_service(service: StoppableService) -> None:
        observed.append(service)
        service.request_stop()
        await service.run_forever()

    monkeypatch.setattr(worker_cli, "WorkerClient", unexpected_client)
    monkeypatch.setattr(worker_cli, "run_with_signal_handlers", stop_idle_service)

    event_logger = WorkerEventLogger(logging.getLogger("test.zero-capability.events"))
    caplog.set_level(logging.INFO, logger="test.zero-capability.events")
    await worker_cli.run_worker(settings, None, event_logger)

    assert len(observed) == 1
    assert isinstance(observed[0], IdleWorker)
    assert observed[0].capability_count == 0
    events = [
        json.loads(record.getMessage())["event"]
        for record in caplog.records
        if record.name == "test.zero-capability.events"
    ]
    assert events == ["stop"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_available", [False, True])
async def test_runtime_advertises_and_routes_ocr_only_after_both_startup_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    handler_available: bool,
) -> None:
    settings = WorkerSettings(
        _env_file=None,
        mode=WorkerMode.TEST,
        work_root=tmp_path / "worker",
        local_storage_root=tmp_path / "storage",
    )
    ready = OcrCapabilityStatus(
        ready=True,
        reason_code=None,
        supports_ocr=True,
        supports_pp_structure=True,
        cached_file_count=1,
    )

    async def native_handler(*_args: object) -> TaskResult:
        raise AssertionError("not dispatched")

    async def ocr_handler(*_args: object) -> TaskResult:
        raise AssertionError("not dispatched")

    async def prepared(
        prepared_settings: WorkerSettings,
        sandboxes: object,
    ) -> tuple[OcrCapabilityStatus, TaskHandler | None]:
        del sandboxes
        assert prepared_settings is settings
        return (
            (ready, ocr_handler)
            if handler_available
            else (OcrCapabilityStatus.unavailable("OCR_HANDLER_UNAVAILABLE"), None)
        )

    captured: dict[str, object] = {}

    def client(**kwargs: object) -> object:
        captured["client"] = kwargs
        return object()

    def dispatcher(*, parse_handler: TaskHandler, ocr_handler: TaskHandler | None) -> object:
        captured["parse_handler"] = parse_handler
        captured["ocr_handler"] = ocr_handler
        return object()

    def poller(**kwargs: object) -> object:
        captured["poller"] = kwargs
        return object()

    async def run_service(service: object) -> None:
        captured["service"] = service

    monkeypatch.setattr(worker_cli, "_prepare_ocr_runtime", prepared)
    monkeypatch.setattr(worker_cli, "WorkerClient", client)
    monkeypatch.setattr(worker_cli, "Dispatcher", dispatcher)
    monkeypatch.setattr(worker_cli, "Poller", poller)
    monkeypatch.setattr(worker_cli, "run_with_signal_handlers", run_service)

    await worker_cli.run_worker(settings, native_handler)

    capabilities = captured["poller"]["capabilities"]  # type: ignore[index]
    assert captured["parse_handler"] is native_handler
    assert captured["ocr_handler"] is (ocr_handler if handler_available else None)
    assert capabilities.supports_ocr is handler_available
    assert ("ocr-v1" in capabilities.parser_profiles) is handler_available
