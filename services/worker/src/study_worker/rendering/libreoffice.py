"""Fail-closed LibreOffice capability probe and restricted conversion."""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    Sandbox,
)

_VERSION_ARGS = ("--version",)
_RENDER_ARGS = (
    "--headless",
    "--nologo",
    "--nodefault",
    "--nofirststartwizard",
    "--norestore",
    "--convert-to",
    "pdf",
    "--outdir",
    "rendered",
    "render-input.pptx",
)


class RendererStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    UNSTABLE = "unstable"
    STABLE = "stable"


class RendererError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RendererProbe:
    status: RendererStatus
    version: str | None = None


@dataclass(frozen=True, slots=True)
class RenderResult:
    path: Path
    sha256: str
    size_bytes: int
    version: str


class LibreOfficeRenderer:
    def __init__(self, executable: Path) -> None:
        self._executable = executable.expanduser()

    async def probe(self, sandbox: Sandbox) -> RendererProbe:
        if (
            not self._executable.is_absolute()
            or self._executable.is_symlink()
            or not self._executable.is_file()
        ):
            return RendererProbe(status=RendererStatus.UNAVAILABLE)
        try:
            result = await self._runner().run(
                "soffice-version",
                _VERSION_ARGS,
                sandbox=sandbox,
                timeout_seconds=10,
            )
        except ProcessBoundaryError:
            return RendererProbe(status=RendererStatus.UNAVAILABLE)
        if result.returncode != 0:
            return RendererProbe(status=RendererStatus.UNAVAILABLE)
        version = result.stdout.decode("utf-8", errors="replace").strip()[:200]
        return RendererProbe(status=classify_libreoffice_version(version), version=version)

    async def render(self, sandbox: Sandbox, *, timeout_seconds: float) -> RenderResult:
        probe = await self.probe(sandbox)
        if probe.status is RendererStatus.UNAVAILABLE:
            raise RendererError("RENDERER_UNAVAILABLE")
        if probe.status is not RendererStatus.STABLE or probe.version is None:
            raise RendererError("RENDERER_UNSTABLE")
        render_input = sandbox.root / "render-input.pptx"
        try:
            shutil.copyfile(sandbox.input_path, render_input)
        except OSError:
            raise RendererError("RENDER_INPUT_UNAVAILABLE", retryable=True) from None
        output_dir = sandbox.root / "rendered"
        output_dir.mkdir(mode=0o700, exist_ok=True)
        try:
            result = await self._runner().run(
                "soffice-render",
                _RENDER_ARGS,
                sandbox=sandbox,
                timeout_seconds=timeout_seconds,
            )
        except ProcessTimeoutError:
            raise RendererError("RENDERER_TIMEOUT", retryable=True) from None
        except ProcessBoundaryError:
            raise RendererError("RENDERER_FAILED", retryable=True) from None
        if result.returncode != 0:
            raise RendererError("RENDERER_FAILED", retryable=True)
        rendered = output_dir / "render-input.pdf"
        try:
            payload = rendered.read_bytes()
        except OSError:
            raise RendererError("RENDER_OUTPUT_MISSING", retryable=True) from None
        if not payload.startswith(b"%PDF-"):
            raise RendererError("RENDER_OUTPUT_INVALID")
        return RenderResult(
            path=rendered,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            version=probe.version,
        )

    def _runner(self) -> RestrictedProcessRunner:
        return RestrictedProcessRunner(
            (
                CommandPolicy(
                    name="soffice-version",
                    executable=self._executable,
                    validate_args=lambda args: args == _VERSION_ARGS,
                ),
                CommandPolicy(
                    name="soffice-render",
                    executable=self._executable,
                    validate_args=lambda args: args == _RENDER_ARGS,
                ),
            ),
            max_output_bytes=64 * 1024,
            environment={"SAL_USE_VCLPLUGIN": "svp"},
        )


def classify_libreoffice_version(output: str) -> RendererStatus:
    normalized = output.strip()
    if not normalized.startswith("LibreOffice "):
        return RendererStatus.UNSTABLE
    if re.search(r"(?:alpha|beta|dev|rc)", normalized, flags=re.IGNORECASE):
        return RendererStatus.UNSTABLE
    if re.fullmatch(r"LibreOffice \d+\.\d+(?:\.\d+){1,3}(?: [A-Za-z0-9._-]+)?", normalized):
        return RendererStatus.STABLE
    return RendererStatus.UNSTABLE
