"""Stable RFC 9457-style API error contracts."""

from __future__ import annotations

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
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_DELETING = "RESOURCE_DELETING"
    DOCUMENT_DUPLICATE = "DOCUMENT_DUPLICATE"
    EVENT_HISTORY_EXPIRED = "EVENT_HISTORY_EXPIRED"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    VERSION_CONFLICT = "VERSION_CONFLICT"


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
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.retryable = retryable


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
    )
    return JSONResponse(
        status_code=exc.status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    trace_id = get_trace_id() or new_trace_id()
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
