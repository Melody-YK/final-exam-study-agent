"""Stable RFC 9457-style API error contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlparse

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse

from study_agent.observability.trace import get_trace_id, new_trace_id


class ProblemCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    HASH_MISMATCH = "HASH_MISMATCH"
    STATE_CONFLICT = "STATE_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    LEASE_LOST = "LEASE_LOST"
    PARSER_TIMEOUT = "PARSER_TIMEOUT"
    PARSER_OOM = "PARSER_OOM"
    PARSE_QUALITY_FAILED = "PARSE_QUALITY_FAILED"
    EMBEDDING_DIMENSION_CHANGED = "EMBEDDING_DIMENSION_CHANGED"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    PROVIDER_NOT_CONFIGURED = "PROVIDER_NOT_CONFIGURED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    INVALID_CITATION = "INVALID_CITATION"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    ACCOUNT_EMAIL_EXISTS = "ACCOUNT_EMAIL_EXISTS"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_DELETING = "RESOURCE_DELETING"
    DOCUMENT_DUPLICATE = "DOCUMENT_DUPLICATE"
    EVENT_HISTORY_EXPIRED = "EVENT_HISTORY_EXPIRED"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    NOTE_WORKFLOW_DISABLED = "NOTE_WORKFLOW_DISABLED"
    NOTE_PROVIDER_NOT_CONFIGURED = "NOTE_PROVIDER_NOT_CONFIGURED"
    NOTE_PROVIDER_TEMPORARILY_UNAVAILABLE = "NOTE_PROVIDER_TEMPORARILY_UNAVAILABLE"
    NOTE_DOCUMENT_NOT_READY = "NOTE_DOCUMENT_NOT_READY"
    NOTE_REQUEST_LIMIT_EXCEEDED = "NOTE_REQUEST_LIMIT_EXCEEDED"
    NOTE_ACTIVE_BATCH_LIMITED = "NOTE_ACTIVE_BATCH_LIMITED"
    NOTE_SOURCE_CHANGED = "NOTE_SOURCE_CHANGED"
    NOTE_COVERAGE_INCOMPLETE = "NOTE_COVERAGE_INCOMPLETE"
    NOTE_OUTPUT_INVALID = "NOTE_OUTPUT_INVALID"
    NOTE_CITATION_INVALID = "NOTE_CITATION_INVALID"
    NOTE_VERSION_NOT_FOUND = "NOTE_VERSION_NOT_FOUND"
    NOTE_BATCH_NOT_RETRYABLE = "NOTE_BATCH_NOT_RETRYABLE"
    NOTE_EXPORT_UNAVAILABLE = "NOTE_EXPORT_UNAVAILABLE"
    NOTE_EXPORT_REVOKED = "NOTE_EXPORT_REVOKED"
    NOTE_EXPORT_CONTENT_UNSUPPORTED = "NOTE_EXPORT_CONTENT_UNSUPPORTED"
    NOTE_EXPORT_RENDER_FAILED = "NOTE_EXPORT_RENDER_FAILED"


class FieldError(BaseModel):
    """A validation issue tied to a request location."""

    model_config = ConfigDict(extra="forbid")

    location: list[str | int] = Field(default_factory=list)
    code: str = Field(min_length=1, pattern=r"^[A-Z][A-Z0-9_]*$")
    message: str = Field(min_length=1)


class ProblemDetails(BaseModel):
    """Serializable ``application/problem+json`` response body."""

    model_config = ConfigDict(extra="forbid")

    type: str = "about:blank"
    title: str = Field(min_length=1)
    status: int = Field(ge=400, le=599)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    detail: str | None = None
    instance: str | None = None
    trace_id: str = Field(min_length=1, max_length=255)
    retryable: bool = False
    retry_after_ms: int | None = Field(default=None, ge=0)
    field_errors: list[FieldError] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def type_must_be_uri_reference(cls, value: str) -> str:
        if value == "about:blank" or urlparse(value).scheme:
            return value
        raise ValueError("problem type must be an absolute URI or about:blank")


class ApiProblem(Exception):
    """Domain-safe error that can cross the HTTP boundary."""

    def __init__(
        self,
        *,
        status: int,
        code: ProblemCode,
        title: str,
        detail: str | None = None,
        retryable: bool = False,
        retry_after_ms: int | None = None,
    ) -> None:
        safe_detail = sanitize_problem_detail(detail)
        super().__init__(safe_detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = safe_detail
        self.retryable = retryable
        self.retry_after_ms = retry_after_ms


async def api_problem_handler(request: Request, exc: ApiProblem) -> JSONResponse:
    trace_id = get_trace_id() or new_trace_id()
    problem = ProblemDetails(
        type=f"https://study-agent.invalid/problems/{exc.code.value.lower().replace('_', '-')}",
        title=exc.title,
        status=exc.status,
        code=exc.code.value,
        detail=exc.detail,
        instance=request.url.path,
        trace_id=trace_id,
        retryable=exc.retryable,
        retry_after_ms=exc.retry_after_ms,
    )
    return JSONResponse(
        status_code=exc.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )


_SENSITIVE_DETAIL = re.compile(
    r"""
    (?:
        (?<![A-Za-z0-9])
        (?:
            api[\s_-]*key
            | access[\s_-]*token
            | refresh[\s_-]*token
            | authorization
            | bearer
            | client[\s_-]*secret
            | password
            | secret
            | lease[\s_-]*token
            | request[\s_-]*body
            | response[\s_-]*body
            | prompt
            | object[\s_-]*key
            | stack(?:[\s_-]*trace)?
            | traceback
        )
        (?![A-Za-z0-9])
        | \b(?:s3|file)://
        | (?:^|[\s='\"]|\bpath\s*:\s*)\s*[A-Za-z]:[\\/]
        | (?:^|[\s='\"]|\bpath\s*:\s*)\s*\\\\[^\\\s]+\\[^\\\s]+
        | (?:^|[\s='\"]|\bpath\s*:\s*)\s*/
          (?!
              api(?:/|$)
              | worker(?:/|$)
              | healthz(?:[/?#]|$)
              | docs(?:[/?#]|$)
              | redoc(?:[/?#]|$)
              | openapi\.json(?:[?#]|$)
          )
          (?=[^\s'\"]+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def sanitize_problem_detail(detail: str | None) -> str | None:
    """Keep public details actionable without exposing upstream/private data."""

    if detail is None:
        return None
    normalized = " ".join(str(detail).replace("\r", " ").replace("\n", " ").split())
    if not normalized:
        return None
    if len(normalized) > 500 or _SENSITIVE_DETAIL.search(normalized):
        return "请求未完成, 请根据错误码和追踪编号重试或联系管理员。"
    return normalized


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    trace_id = get_trace_id() or new_trace_id()
    if any(
        error.get("type") == "missing" and tuple(error.get("loc", ()))[:2] == ("header", "If-Match")
        for error in exc.errors()
    ):
        problem = ProblemDetails(
            type="https://study-agent.invalid/problems/precondition-required",
            title="需要 If-Match",
            status=428,
            code=ProblemCode.PRECONDITION_REQUIRED.value,
            instance=request.url.path,
            trace_id=trace_id,
        )
        return JSONResponse(
            status_code=428,
            content=problem.model_dump(mode="json"),
            media_type="application/problem+json",
        )
    field_errors = [
        FieldError(
            location=list(error.get("loc", ())),
            code="INVALID_FIELD",
            message=str(error.get("msg", "invalid value")),
        )
        for error in exc.errors()
    ]
    problem = ProblemDetails(
        type="https://study-agent.invalid/problems/invalid-request",
        title="请求参数无效",
        status=422,
        code=ProblemCode.INVALID_REQUEST.value,
        instance=request.url.path,
        trace_id=trace_id,
        field_errors=field_errors,
    )
    return JSONResponse(
        status_code=422,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )
