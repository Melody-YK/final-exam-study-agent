"""Vendor-neutral parser, result, and time boundaries for the local worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from study_contracts import Asset, ParseResultManifest


class ParserExecutionError(RuntimeError):
    """Stable parser failure safe to cross a process and control-plane boundary."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_media_type(value: str) -> None:
    if "/" not in value or value.startswith("/") or value.endswith("/"):
        raise ValueError("media types must use type/subtype values")


@dataclass(frozen=True, slots=True)
class ParserCapability:
    """Static facts used to match jobs with an installed parser adapter."""

    profile: str
    source_backend: str
    source_version: str
    media_types: frozenset[str]
    supports_ocr: bool = False
    supports_rendering: bool = False

    def __post_init__(self) -> None:
        _require_non_blank(self.profile, "profile")
        _require_non_blank(self.source_backend, "source_backend")
        _require_non_blank(self.source_version, "source_version")
        if not self.media_types:
            raise ValueError("media_types must not be empty")
        for media_type in self.media_types:
            _require_media_type(media_type)


@dataclass(frozen=True, slots=True)
class ParseRequest:
    """A sandbox-scoped parser invocation without transport credentials."""

    job_id: str
    document_id: str
    document_sha256: str
    media_type: str
    input_path: Path
    output_dir: Path
    requested_pages: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _require_non_blank(self.job_id, "job_id")
        _require_non_blank(self.document_id, "document_id")
        if len(self.document_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.document_sha256
        ):
            raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")
        _require_media_type(self.media_type)
        if any(ordinal < 1 for ordinal in self.requested_pages):
            raise ValueError("requested page ordinals must be positive")
        if len(self.requested_pages) != len(set(self.requested_pages)):
            raise ValueError("requested page ordinals must be unique")


@dataclass(frozen=True, slots=True)
class ParserResult:
    """A normalized manifest plus immutable derived assets."""

    manifest: ParseResultManifest
    assets: tuple[Asset, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("asset identifiers must be unique")
        page_ordinals = {page.ordinal for page in self.manifest.pages}
        if any(asset.locator.ordinal not in page_ordinals for asset in self.assets):
            raise ValueError("every asset must reference a page present in the manifest")


@runtime_checkable
class Parser(Protocol):
    """Parser adapter contract; implementations arrive in the native/OCR phases."""

    @property
    def capability(self) -> ParserCapability: ...

    async def parse(self, request: ParseRequest) -> ParserResult: ...


@runtime_checkable
class Clock(Protocol):
    """Injectable time source for poll, retry, and lease logic."""

    def now(self) -> datetime: ...
