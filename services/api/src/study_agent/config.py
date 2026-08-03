"""Validated runtime configuration for the API control plane."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMode(StrEnum):
    """Supported application safety profiles."""

    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    """Object storage implementations available at runtime."""

    LOCAL = "local"
    OSS = "oss"


type EmbeddingProviderName = Literal["openai-compatible"]
type ChatProviderName = Literal["deepseek"]
type AuthProviderName = Literal["oidc"]


class Settings(BaseSettings):
    """Application settings with fail-closed production validation.

    Provider credentials are optional in local and test modes so document
    parsing and browsing remain usable without model access. Production auth
    and the worker boundary are different: they must be configured before the
    process may start.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    app_mode: AppMode = AppMode.LOCAL
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://study_agent@127.0.0.1:54329/study_agent"
    )
    storage_backend: StorageBackend = StorageBackend.LOCAL
    local_storage_root: Path = Path(".local/storage")
    lexical_index_root: Path = Path(".local/lexical")
    course_terms: tuple[str, ...] = ()
    oss_endpoint: str | None = None
    oss_bucket: str | None = None
    oss_region: str | None = None

    embedding_provider: EmbeddingProviderName = "openai-compatible"
    embedding_base_url: str = "https://router.tumuer.me/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: SecretStr | None = None
    embedding_dimensions: int | None = Field(default=None, gt=0)
    embedding_batch_size: int = Field(default=64, ge=1, le=512)
    index_runner_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    chat_provider: ChatProviderName = "deepseek"
    chat_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-v4-flash"
    deepseek_api_key: SecretStr | None = None
    chat_stream: bool = True

    provider_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    provider_max_attempts: int = Field(default=3, ge=1, le=8)
    provider_retry_base_seconds: float = Field(default=0.5, ge=0, le=30)
    provider_retry_max_seconds: float = Field(default=8.0, ge=0, le=60)
    provider_max_response_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    provider_max_stream_events: int = Field(default=4096, ge=1, le=100_000)
    provider_max_stream_event_bytes: int = Field(default=256 * 1024, ge=256)
    provider_max_answer_chars: int = Field(default=1024 * 1024, ge=256)
    query_requests_per_minute: int = Field(default=30, ge=1, le=10_000)
    upload_requests_per_minute: int = Field(default=60, ge=1, le=10_000)
    active_account_capacity: int = Field(default=10, ge=1, le=10_000)

    auth_provider: AuthProviderName | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_client_secret: SecretStr | None = None
    worker_token: SecretStr | None = None
    worker_lease_seconds: int = Field(default=30, ge=5, le=300)
    worker_presence_ttl_seconds: int = Field(default=45, ge=5, le=300)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    job_event_retention_seconds: int = Field(default=24 * 60 * 60, ge=60)
    sse_heartbeat_seconds: int = Field(default=15, ge=1, le=60)

    # Note workflow controls are deliberately disabled by default.  The
    # runner/capability checks below make enabling them fail closed.
    note_async_workflow_enabled: bool = False
    note_runner_enabled: bool = False
    note_runner_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    note_runner_lease_seconds: int = Field(default=60, ge=5, le=900)
    note_demo_phase_delay_seconds: float = Field(default=0.35, ge=0, le=5)
    note_generation_max_attempts: int = Field(default=3, ge=1, le=20)
    note_batch_max_documents: int = Field(default=20, ge=1, le=500)
    note_batch_max_coverage_units: int = Field(default=2_000, ge=1, le=100_000)
    note_batch_max_pages: int = Field(default=4_000, ge=1, le=1_000_000)
    note_batch_max_estimated_tokens: int = Field(default=1_000_000, ge=1_000, le=100_000_000)
    note_max_active_batches_per_user: int = Field(default=3, ge=1, le=100)
    note_command_dedup_retention_seconds: int = Field(default=7 * 24 * 60 * 60, ge=1)
    note_event_retention_seconds: int = Field(default=24 * 60 * 60, ge=60)
    note_eta_min_success_samples: int = Field(default=20, ge=1, le=10_000)
    note_numeric_eta_enabled: bool = False
    note_docx_export_enabled: bool = False
    note_export_runner_enabled: bool = False
    note_export_max_attempts: int = Field(default=3, ge=1, le=20)
    note_export_lease_seconds: int = Field(default=120, ge=5, le=1_800)
    note_export_max_bytes: int = Field(default=64 * 1024 * 1024, ge=1_024, le=2**31)
    note_export_retention_seconds: int = Field(default=7 * 24 * 60 * 60, ge=60)
    note_docx_render_timeout_seconds: int = Field(default=180, ge=1, le=3_600)
    note_docx_images_enabled: bool = False
    note_docx_renderer_enabled: bool = False
    note_docx_cjk_font_family: str = "Noto Sans CJK SC"

    # Active-recall generation is disabled by default and only accepts work
    # when a real JSON-capable chat provider is configured.
    practice_generation_enabled: bool = False
    practice_runner_enabled: bool = False
    practice_runner_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    practice_runner_lease_seconds: int = Field(default=60, ge=5, le=900)
    practice_generation_max_attempts: int = Field(default=3, ge=1, le=20)
    practice_batch_max_questions: int = Field(default=10, ge=1, le=10)
    practice_max_active_batches_per_user: int = Field(default=3, ge=1, le=100)

    reranker_enabled: bool = False
    complex_parser_enabled: bool = False
    partial_ready_enabled: bool = False
    demo_lab_enabled: bool = True

    max_upload_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=2_000, gt=0)
    max_pixels: int = Field(default=100_000_000, gt=0)
    external_process_timeout_seconds: int = Field(default=180, gt=0, le=3_600)
    soffice_bin: Path | None = None

    @field_validator(
        "embedding_api_key",
        "deepseek_api_key",
        "auth_client_secret",
        "worker_token",
        mode="before",
    )
    @classmethod
    def empty_secret_is_unconfigured(cls, value: object) -> object:
        """Treat empty environment variables as absent credentials."""

        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @field_validator(
        "oss_endpoint",
        "oss_bucket",
        "oss_region",
        "auth_issuer",
        "auth_audience",
        mode="before",
    )
    @classmethod
    def empty_optional_text_is_unconfigured(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @field_validator("course_terms", mode="after")
    @classmethod
    def normalize_course_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted({term.strip().lower() for term in value if term.strip()}))

    @field_validator("allowed_hosts", mode="after")
    @classmethod
    def normalize_allowed_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for host in value:
            normalized_host = normalize_host(host, allow_wildcard=True)
            if normalized_host and normalized_host not in normalized:
                normalized.append(normalized_host)
        return tuple(normalized)

    @field_validator("allowed_origins", mode="after")
    @classmethod
    def normalize_allowed_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for origin in value:
            stripped = origin.strip().rstrip("/")
            if not stripped:
                continue
            parsed = urlsplit(stripped)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("allowed origins must be absolute HTTP(S) origins")
            if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise ValueError("allowed origins must not contain paths or credentials")
            normalized.append(stripped.lower())
        return tuple(dict.fromkeys(normalized))

    @field_validator(
        "embedding_base_url",
        "chat_base_url",
        "embedding_model",
        "chat_model",
        "note_docx_cjk_font_family",
    )
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("embedding_base_url", "chat_base_url")
    @classmethod
    def provider_base_url_must_be_safe(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider base URL must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("provider base URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("provider base URL must not contain query or fragment data")
        return value.rstrip("/")

    @field_validator("local_storage_root", "lexical_index_root", mode="after")
    @classmethod
    def resolve_local_storage_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("soffice_bin", mode="before")
    @classmethod
    def normalize_optional_executable(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("soffice_bin", mode="after")
    @classmethod
    def resolve_optional_executable(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def enforce_runtime_boundaries(self) -> Self:
        try:
            bind_address = ip_address(self.bind_host)
        except ValueError as exc:
            raise ValueError("bind_host must be an IP address") from exc

        if self.app_mode is AppMode.LOCAL and not bind_address.is_loopback:
            raise ValueError("local mode may only bind to a loopback address")

        if self.app_mode is not AppMode.TEST and "*" in self.allowed_hosts:
            raise ValueError("non-test mode does not allow the global wildcard host")
        if self.app_mode is AppMode.LOCAL and any(
            host.startswith("*.") for host in self.allowed_hosts
        ):
            raise ValueError("local mode does not allow wildcard hosts")

        if self.provider_retry_max_seconds < self.provider_retry_base_seconds:
            raise ValueError("provider retry maximum must be at least the base delay")

        if self.note_command_dedup_retention_seconds < self.note_event_retention_seconds:
            raise ValueError("note dedup retention must not be shorter than event retention")
        if self.note_batch_max_coverage_units < self.note_batch_max_documents:
            raise ValueError("note coverage-unit limit must cover the document limit")
        if self.note_async_workflow_enabled:
            missing: list[str] = []
            if not self.note_runner_enabled:
                missing.append("note_runner_enabled")
            if not self.chat_configured:
                missing.append("deepseek_api_key")
            if not self.embedding_configured:
                missing.append("embedding_api_key")
            if missing:
                raise ValueError(
                    "note workflow requires configured capabilities: " + ", ".join(missing)
                )
        if self.note_numeric_eta_enabled and not self.note_async_workflow_enabled:
            raise ValueError("numeric ETA requires note_async_workflow_enabled")
        if self.note_docx_export_enabled:
            missing_docx: list[str] = []
            if not self.note_async_workflow_enabled:
                missing_docx.append("note_async_workflow_enabled")
            if not self.note_export_runner_enabled:
                missing_docx.append("note_export_runner_enabled")
            if not self.note_docx_renderer_enabled:
                missing_docx.append("note_docx_renderer_enabled")
            if missing_docx:
                raise ValueError(
                    "DOCX export requires configured capabilities: " + ", ".join(missing_docx)
                )

        secret_endpoints = (
            (self.embedding_api_key, self.embedding_base_url),
            (self.deepseek_api_key, self.chat_base_url),
        )
        if self.app_mode is not AppMode.TEST and any(
            secret is not None and urlsplit(base_url).scheme != "https"
            for secret, base_url in secret_endpoints
        ):
            raise ValueError("configured provider credentials require HTTPS endpoints")

        oss_values = (self.oss_endpoint, self.oss_bucket, self.oss_region)
        if self.storage_backend is StorageBackend.OSS and not all(oss_values):
            raise ValueError("OSS storage requires endpoint, bucket, and region")
        if self.storage_backend is StorageBackend.LOCAL and any(oss_values):
            raise ValueError("OSS settings require storage_backend=oss")

        if self.app_mode is AppMode.PRODUCTION:
            missing_production: list[str] = []
            if self.auth_provider != "oidc":
                missing_production.append("auth_provider")
            if self.auth_issuer is None:
                missing_production.append("auth_issuer")
            if self.auth_audience is None:
                missing_production.append("auth_audience")
            if self.worker_token is None:
                missing_production.append("worker_token")
            if not self.allowed_hosts:
                missing_production.append("allowed_hosts")
            if not self.allowed_origins:
                missing_production.append("allowed_origins")
            if missing_production:
                fields = ", ".join(missing_production)
                raise ValueError(f"production configuration is missing: {fields}")
            if self.worker_token is not None:
                _validate_production_worker_token(self.worker_token)
            if any(urlsplit(origin).scheme != "https" for origin in self.allowed_origins):
                raise ValueError("production allowed origins must use HTTPS")

        return self

    @property
    def embedding_configured(self) -> bool:
        return self.embedding_api_key is not None

    @property
    def chat_configured(self) -> bool:
        return self.deepseek_api_key is not None

    @property
    def providers_configured(self) -> bool:
        """Whether both runtime model capabilities can be constructed."""

        return self.embedding_configured and self.chat_configured

    @property
    def note_workflow_configured(self) -> bool:
        """Whether the asynchronous generation gate can safely accept work."""

        return (
            self.note_async_workflow_enabled
            and self.note_runner_enabled
            and self.providers_configured
        )

    @property
    def note_docx_configured(self) -> bool:
        """Whether DOCX export has all independent capability gates enabled."""

        return (
            self.note_docx_export_enabled
            and self.note_workflow_configured
            and self.note_export_runner_enabled
            and self.note_docx_renderer_enabled
        )

    @property
    def effective_allowed_hosts(self) -> tuple[str, ...]:
        if self.allowed_hosts:
            return self.allowed_hosts
        if self.app_mode is AppMode.TEST:
            return ("testserver", "127.0.0.1", "localhost", "::1")
        return ("127.0.0.1", "localhost", "::1")

    @property
    def effective_allowed_origins(self) -> tuple[str, ...]:
        if self.allowed_origins:
            return self.allowed_origins
        if self.app_mode is AppMode.TEST:
            return ("http://testserver",)
        ports = (self.bind_port, 5173)
        return tuple(
            f"http://{host}:{port}"
            for host in ("127.0.0.1", "localhost", "[::1]")
            for port in ports
        )


def normalize_host(value: str, *, allow_wildcard: bool = False) -> str:
    host = value.strip().lower()
    if not host:
        return ""
    if not host.isascii() or any(
        character.isspace() or character in "/\\@?#%" for character in host
    ):
        raise ValueError("hosts must contain ASCII host names only")
    if host == "*":
        if allow_wildcard:
            return host
        raise ValueError("hosts contain an invalid wildcard")

    wildcard = allow_wildcard and host.startswith("*.")
    if "*" in host and (not wildcard or host.count("*") != 1):
        raise ValueError("hosts contain an invalid wildcard")
    candidate = host[2:] if wildcard else host
    if not candidate or candidate.startswith(".") or candidate.endswith(".") or ".." in candidate:
        raise ValueError("hosts contain an invalid host name")

    if candidate.startswith("["):
        if wildcard or not candidate.endswith("]") or candidate.count("[") != 1:
            raise ValueError("hosts contain an invalid IPv6 literal")
        try:
            address = ip_address(candidate[1:-1])
        except ValueError as exc:
            raise ValueError("hosts contain an invalid IP address") from exc
        if address.version != 6:
            raise ValueError("brackets are only valid for IPv6 hosts")
        return address.compressed
    if "[" in candidate or "]" in candidate:
        raise ValueError("hosts contain an invalid IPv6 literal")

    if ":" in candidate:
        if wildcard:
            raise ValueError("wildcard hosts require a DNS suffix")
        try:
            address = ip_address(candidate)
        except ValueError as exc:
            raise ValueError("hosts must not include a port") from exc
        if address.version != 6:
            raise ValueError("hosts must not include a port")
        return address.compressed

    try:
        address = ip_address(candidate)
    except ValueError:
        if len(candidate) > 253:
            raise ValueError("host name is too long") from None
        labels = candidate.split(".")
        if any(
            len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or any(not (character.isalnum() or character == "-") for character in label)
            for label in labels
        ):
            raise ValueError("hosts contain an invalid host name") from None
        normalized = candidate
    else:
        if wildcard:
            raise ValueError("wildcard hosts require a DNS suffix")
        normalized = address.compressed

    return f"*.{normalized}" if wildcard else normalized


def _validate_production_worker_token(token: SecretStr) -> None:
    value = token.get_secret_value()
    if len(value) < 32 or len(set(value)) < 8:
        raise ValueError(
            "production worker token must be at least 32 characters with sufficient diversity"
        )
    normalized = "".join(character for character in value.lower() if character.isalnum())
    if any(marker in normalized for marker in ("password", "workersecret", "changeme")):
        raise ValueError("production worker token must not use a common placeholder")
