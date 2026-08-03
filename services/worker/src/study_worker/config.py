"""Validated, fail-closed settings for the local pull worker."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerMode(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class WorkerSettings(BaseSettings):
    """Worker configuration without parser or transport implementations."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WORKER_",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    mode: WorkerMode = WorkerMode.LOCAL
    api_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    token: SecretStr | None = None
    instance_id: str = Field(default="local-worker", min_length=1, max_length=128)
    work_root: Path = Path(".local/worker")
    local_storage_root: Path = Path(".local/storage")

    poll_wait_seconds: int = Field(default=20, ge=1, le=30)
    request_timeout_seconds: int = Field(default=30, ge=2, le=120)
    heartbeat_interval_seconds: int = Field(default=10, ge=1, le=60)
    poll_backoff_initial_seconds: float = Field(default=0.5, gt=0, le=60)
    poll_backoff_max_seconds: float = Field(default=30, gt=0, le=300)
    external_process_timeout_seconds: int = Field(default=180, ge=1, le=3_600)

    max_input_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=2_000, gt=0)
    max_pixels: int = Field(default=100_000_000, gt=0)
    soffice_bin: Path | None = None
    paddle_profile_bin: Path | None = None
    paddle_model_cache: Path | None = None
    docling_profile_bin: Path | None = None
    docling_artifacts_root: Path | None = None
    mineru_base_url: AnyHttpUrl | None = None
    mineru_token: SecretStr | None = None
    mineru_backend: Literal["pipeline"] = "pipeline"
    complex_parser_enabled: bool = False

    @field_validator("token", "mineru_token", mode="before")
    @classmethod
    def empty_token_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("instance_id")
    @classmethod
    def normalize_instance_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("instance_id must not be blank")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in stripped):
            raise ValueError("instance_id must not contain control characters")
        return stripped

    @field_validator("work_root", "local_storage_root", mode="after")
    @classmethod
    def resolve_runtime_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator(
        "soffice_bin",
        "paddle_profile_bin",
        "paddle_model_cache",
        "docling_profile_bin",
        "docling_artifacts_root",
        mode="before",
    )
    @classmethod
    def empty_soffice_path_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("soffice_bin", mode="after")
    @classmethod
    def resolve_soffice_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    @field_validator(
        "paddle_profile_bin",
        "paddle_model_cache",
        "docling_profile_bin",
        "docling_artifacts_root",
        mode="after",
    )
    @classmethod
    def make_optional_ocr_path_absolute(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().absolute()

    @model_validator(mode="after")
    def enforce_runtime_boundaries(self) -> Self:
        if self.mode is not WorkerMode.TEST and self.token is None:
            raise ValueError("worker token is required outside test mode")

        host = self.api_base_url.host
        if host is None:
            raise ValueError("worker API URL must include a host")
        if self.api_base_url.username is not None or self.api_base_url.password is not None:
            raise ValueError("worker API URL must not include credentials")
        if self.api_base_url.query is not None or self.api_base_url.fragment is not None:
            raise ValueError("worker API URL must not include query or fragment components")
        if self.mode is WorkerMode.LOCAL:
            try:
                loopback = ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                raise ValueError("local worker may only connect to a loopback API")

        if self.mode is WorkerMode.PRODUCTION and self.api_base_url.scheme != "https":
            raise ValueError("production worker API must use HTTPS")
        if self.mode is WorkerMode.PRODUCTION and self.token is not None:
            _validate_production_token(self.token)

        if self.mineru_base_url is not None:
            mineru_host = self.mineru_base_url.host
            if mineru_host is None:
                raise ValueError("MinerU URL must include a host")
            if (
                self.mineru_base_url.username is not None
                or self.mineru_base_url.password is not None
                or self.mineru_base_url.query is not None
                or self.mineru_base_url.fragment is not None
            ):
                raise ValueError("MinerU URL must not include credentials, query, or fragment")
            try:
                mineru_loopback = ip_address(mineru_host).is_loopback
            except ValueError:
                mineru_loopback = mineru_host == "localhost"
            if (
                self.mode is WorkerMode.PRODUCTION
                and self.mineru_base_url.scheme != "https"
                and not mineru_loopback
            ):
                raise ValueError("production MinerU URL must use HTTPS unless it is loopback")

        if self.request_timeout_seconds <= self.poll_wait_seconds:
            raise ValueError("request timeout must exceed the long-poll wait")
        if self.poll_backoff_max_seconds < self.poll_backoff_initial_seconds:
            raise ValueError("maximum poll backoff must not be below the initial backoff")
        if self.work_root == self.local_storage_root:
            raise ValueError("worker work root and local storage root must be distinct")
        return self


def _validate_production_token(token: SecretStr) -> None:
    value = token.get_secret_value()
    if len(value) < 32 or len(set(value)) < 8:
        raise ValueError(
            "production worker token must be at least 32 characters with sufficient diversity"
        )
    normalized = "".join(character for character in value.lower() if character.isalnum())
    if any(marker in normalized for marker in ("password", "workersecret", "changeme")):
        raise ValueError("production worker token must not use a common placeholder")
