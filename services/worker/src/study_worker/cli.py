"""Typer entrypoint for worker configuration and the persistent poller."""

from __future__ import annotations

import asyncio
import json
from importlib import metadata
from typing import cast

import typer
from pydantic import ValidationError

from study_worker import __version__
from study_worker.capabilities import OcrCapabilityStatus, probe_paddle_profile
from study_worker.config import WorkerSettings
from study_worker.dispatcher import Dispatcher, TaskHandler, capabilities_for_handlers
from study_worker.poller.client import WorkerClient
from study_worker.poller.poller import (
    IdleWorker,
    Poller,
    WorkerEventLogger,
    run_with_signal_handlers,
)
from study_worker.runtime import build_ocr_handler
from study_worker.sandbox import SandboxManager

app = typer.Typer(
    name="study-agent-worker",
    help="受限的本地文档解析 Worker。",
    no_args_is_help=True,
)


def _load_settings() -> WorkerSettings:
    try:
        return WorkerSettings()
    except ValidationError:
        typer.echo("Worker 配置无效; 请检查必填项和运行模式边界。", err=True)
        raise typer.Exit(code=2) from None


@app.command("config-check")
def config_check() -> None:
    """Validate settings without displaying credentials."""

    settings = _load_settings()
    typer.echo(
        "Worker 配置有效: "
        f"mode={settings.mode.value} "
        f"api={settings.api_base_url} "
        f"instance={settings.instance_id} "
        f"work_root={settings.work_root}"
    )


@app.command()
def version() -> None:
    """Print the installed worker package version."""

    typer.echo(__version__)


@app.command("ocr-capabilities")
def ocr_capabilities() -> None:
    """Probe the configured isolated OCR profile without loading a model."""

    settings = _load_settings()
    sandboxes = SandboxManager(settings.work_root)

    status, handler = asyncio.run(_prepare_ocr_runtime(settings, sandboxes))
    payload = {
        "ready": status.ready,
        "reason_code": status.reason_code,
        "supports_ocr": status.supports_ocr,
        "supports_pp_structure": status.supports_pp_structure,
        "versions": dict(status.versions),
        "cached_file_count": status.cached_file_count,
        "claim_enabled": handler is not None,
    }
    typer.echo(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    if not status.ready:
        raise typer.Exit(code=3)


class NativeHandlerUnavailable(RuntimeError):
    """An installed native handler entry point is invalid or ambiguous."""


NATIVE_HANDLER_ENTRY_POINT_GROUP = "study_agent.worker_handlers"
NATIVE_HANDLER_ENTRY_POINT_NAME = "native-v1"


def _native_task_handler(settings: WorkerSettings) -> TaskHandler | None:
    """Discover the one explicitly named native handler, if installed."""

    candidates = [
        entry_point
        for entry_point in metadata.entry_points(group=NATIVE_HANDLER_ENTRY_POINT_GROUP)
        if entry_point.name == NATIVE_HANDLER_ENTRY_POINT_NAME
    ]
    if not candidates:
        return None
    if len(candidates) != 1:
        raise NativeHandlerUnavailable("multiple native handler entry points")
    try:
        factory = candidates[0].load()
        if not callable(factory):
            raise TypeError("native handler entry point must be a factory")
        handler = factory(settings)
    except Exception:
        raise NativeHandlerUnavailable("native handler discovery failed") from None
    if not callable(handler):
        raise NativeHandlerUnavailable("native handler factory returned a non-callable")
    return cast(TaskHandler, handler)


async def run_worker(
    settings: WorkerSettings,
    parse_handler: TaskHandler | None,
    event_logger: WorkerEventLogger | None = None,
) -> None:
    """Assemble the runtime and advertise OCR only after probe and handler gates pass."""

    if parse_handler is None:
        await run_with_signal_handlers(IdleWorker(event_logger))
        return

    sandboxes = SandboxManager(settings.work_root)
    ocr_status, ocr_handler = await _prepare_ocr_runtime(settings, sandboxes)
    capabilities = capabilities_for_handlers(
        max_input_bytes=settings.max_input_bytes,
        max_pages=settings.max_pages,
        ocr_status=ocr_status,
        ocr_handler_available=ocr_handler is not None,
    )
    client = WorkerClient(
        base_url=str(settings.api_base_url),
        worker_id=settings.instance_id,
        token=settings.token,
        timeout_seconds=settings.request_timeout_seconds,
        local_storage_root=settings.local_storage_root,
    )
    poller = Poller(
        client=client,
        dispatcher=Dispatcher(parse_handler=parse_handler, ocr_handler=ocr_handler),
        sandboxes=sandboxes,
        worker_id=settings.instance_id,
        capabilities=capabilities,
        event_logger=event_logger,
        poll_wait_seconds=settings.poll_wait_seconds,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        task_timeout_seconds=settings.external_process_timeout_seconds,
        backoff_initial_seconds=settings.poll_backoff_initial_seconds,
        backoff_max_seconds=settings.poll_backoff_max_seconds,
    )
    await run_with_signal_handlers(poller)


async def _prepare_ocr_runtime(
    settings: WorkerSettings,
    sandboxes: SandboxManager,
) -> tuple[OcrCapabilityStatus, TaskHandler | None]:
    with sandboxes.create() as sandbox:
        status = await probe_paddle_profile(
            executable=settings.paddle_profile_bin,
            model_cache=settings.paddle_model_cache,
            sandbox=sandbox,
            timeout_seconds=min(10, settings.external_process_timeout_seconds),
        )
    if not status.ready:
        return status, None
    try:
        return status, build_ocr_handler(settings, status)
    except (OSError, ValueError):
        return OcrCapabilityStatus.unavailable("OCR_HANDLER_UNAVAILABLE"), None


@app.command()
def run() -> None:
    """Run the persistent worker after a native parser handler is installed."""

    settings = _load_settings()
    WorkerEventLogger.configure_stderr()
    try:
        parse_handler = _native_task_handler(settings)
    except NativeHandlerUnavailable:
        typer.echo("Native handler 加载失败; 已安全降级为零能力 idle 模式。", err=True)
        parse_handler = None
    if parse_handler is None:
        typer.echo("Worker 已启动: state=idle capabilities=0 claims=disabled")
    else:
        typer.echo("Worker 已启动: state=polling capabilities=native-v1 ocr=probe-at-start")
    try:
        asyncio.run(run_worker(settings, parse_handler))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    app()
