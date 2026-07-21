"""Fail-closed probes for optional parser runtimes outside the base Worker environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from study_worker.sandbox import (
    CommandPolicy,
    ProcessBoundaryError,
    ProcessTimeoutError,
    RestrictedProcessRunner,
    Sandbox,
)

OCR_PROFILE = "ocr-v1"
ISOLATED_PADDLE_PROFILE = "paddle-ocr-v1"
_VERSION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,99}")


class _PaddleProfileReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["paddle-ocr-v1"]
    ready: bool
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    platform: str = Field(min_length=1, max_length=64)
    python: str = Field(min_length=1, max_length=64)
    versions: dict[str, str] = Field(default_factory=dict)
    missing_packages: list[str] = Field(default_factory=list)
    cache_root: str = Field(min_length=1)
    cached_file_count: int = Field(ge=0)
    supports_ocr: bool
    supports_pp_structure: bool
    supports_mineru: Literal[False]
    supports_paid_ocr: Literal[False]

    @field_validator("versions")
    @classmethod
    def versions_must_be_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 32 or any(
            not name.strip()
            or len(name) > 100
            or not version.strip()
            or len(version) > 100
            or _VERSION_TOKEN.fullmatch(name) is None
            or _VERSION_TOKEN.fullmatch(version) is None
            for name, version in value.items()
        ):
            raise ValueError("profile versions are invalid")
        return value

    @field_validator("missing_packages")
    @classmethod
    def missing_packages_must_be_bounded(cls, value: list[str]) -> list[str]:
        if (
            len(value) > 32
            or len(value) != len(set(value))
            or any(not package.strip() or len(package) > 100 for package in value)
        ):
            raise ValueError("missing package list is invalid")
        return value

    @model_validator(mode="after")
    def readiness_fields_must_agree(self) -> _PaddleProfileReport:
        if self.ready:
            if (
                self.reason_code is not None
                or not self.supports_ocr
                or self.cached_file_count < 1
                or self.missing_packages
            ):
                raise ValueError("ready profile report contains unavailable capability facts")
        elif self.reason_code is None or self.supports_ocr or self.supports_pp_structure:
            raise ValueError("unavailable profile report must fail closed")
        return self


@dataclass(frozen=True, slots=True)
class OcrCapabilityStatus:
    """Sanitized capability facts safe to use for Worker claim construction."""

    ready: bool
    reason_code: str | None
    supports_ocr: bool = False
    supports_pp_structure: bool = False
    versions: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    cached_file_count: int = 0

    def __post_init__(self) -> None:
        if self.ready:
            if self.reason_code is not None or not self.supports_ocr or self.cached_file_count < 1:
                raise ValueError("ready OCR capability status is inconsistent")
        elif (
            self.reason_code is None
            or self.supports_ocr
            or self.supports_pp_structure
            or self.versions
            or self.cached_file_count
        ):
            raise ValueError("unavailable OCR capability status must fail closed")

    @classmethod
    def unavailable(cls, reason_code: str) -> OcrCapabilityStatus:
        return cls(ready=False, reason_code=reason_code)


async def probe_paddle_profile(
    *,
    executable: Path | None,
    model_cache: Path | None,
    sandbox: Sandbox,
    timeout_seconds: float = 10,
    max_output_bytes: int = 64 * 1024,
) -> OcrCapabilityStatus:
    """Probe an isolated executable without importing Paddle or permitting downloads."""

    if executable is None:
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_NOT_CONFIGURED")
    if model_cache is None:
        return OcrCapabilityStatus.unavailable("OCR_MODELS_NOT_CONFIGURED")
    if timeout_seconds <= 0 or max_output_bytes <= 0:
        raise ValueError("probe limits must be positive")

    configured_executable = executable.expanduser()
    if (
        not configured_executable.is_absolute()
        or configured_executable.is_symlink()
        or not configured_executable.is_file()
        or not os.access(configured_executable, os.X_OK)
    ):
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_UNAVAILABLE")

    configured_cache = model_cache.expanduser()
    if not configured_cache.is_absolute() or configured_cache.is_symlink():
        return OcrCapabilityStatus.unavailable("OCR_MODELS_NOT_CACHED")
    try:
        resolved_cache = configured_cache.resolve(strict=True)
    except OSError:
        return OcrCapabilityStatus.unavailable("OCR_MODELS_NOT_CACHED")
    if not resolved_cache.is_dir() or not _contains_model_file(resolved_cache):
        return OcrCapabilityStatus.unavailable("OCR_MODELS_NOT_CACHED")

    expected_args = ("capabilities", "--cache-root", str(resolved_cache))
    try:
        runner = RestrictedProcessRunner(
            (
                CommandPolicy(
                    name="paddle-capabilities",
                    executable=configured_executable,
                    validate_args=lambda args: args == expected_args,
                ),
            ),
            max_output_bytes=max_output_bytes,
        )
        result = await runner.run(
            "paddle-capabilities",
            expected_args,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
        )
    except ProcessTimeoutError:
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_PROBE_TIMEOUT")
    except (ProcessBoundaryError, OSError, ValueError):
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_PROBE_FAILED")

    try:
        report = _PaddleProfileReport.model_validate_json(result.stdout)
    except (ValidationError, ValueError):
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_REPORT_INVALID")
    try:
        reported_cache = Path(report.cache_root).expanduser().resolve(strict=True)
    except OSError:
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_REPORT_INVALID")
    if reported_cache != resolved_cache:
        return OcrCapabilityStatus.unavailable("OCR_PROFILE_REPORT_INVALID")
    if report.cached_file_count < 1 or not _contains_model_file(resolved_cache):
        return OcrCapabilityStatus.unavailable("OCR_MODELS_NOT_CACHED")
    if result.returncode == 0 and report.ready:
        return OcrCapabilityStatus(
            ready=True,
            reason_code=None,
            supports_ocr=True,
            supports_pp_structure=report.supports_pp_structure,
            versions=tuple(sorted(report.versions.items())),
            cached_file_count=report.cached_file_count,
        )
    if result.returncode == 3 and not report.ready and report.reason_code is not None:
        return OcrCapabilityStatus.unavailable(report.reason_code)
    return OcrCapabilityStatus.unavailable("OCR_PROFILE_REPORT_INVALID")


def _contains_model_file(cache_root: Path) -> bool:
    try:
        for path in cache_root.rglob("*"):
            if path.is_symlink():
                continue
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False
