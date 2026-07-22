import json

import pytest
from starlette.requests import Request

from study_agent.api.errors import (
    ApiProblem,
    ProblemCode,
    ProblemDetails,
    api_problem_handler,
    sanitize_problem_detail,
)
from study_agent.observability.redaction import redact

GENERIC_PUBLIC_DETAIL = "请求未完成, 请根据错误码和追踪编号重试或联系管理员。"


def test_problem_details_has_stable_machine_code_and_trace() -> None:
    problem = ProblemDetails(
        type="https://study-agent.invalid/problems/provider-not-configured",
        title="模型服务尚未连接",
        status=503,
        code="PROVIDER_NOT_CONFIGURED",
        detail="请配置真实模型服务后重试。",
        trace_id="trace-1",
        retryable=False,
    )

    assert problem.model_dump(mode="json")["code"] == "PROVIDER_NOT_CONFIGURED"
    assert problem.model_dump(mode="json")["trace_id"] == "trace-1"


def test_redaction_masks_nested_credentials_and_authorization_headers() -> None:
    data = {
        "authorization": "Bearer secret-value",
        "request": {
            "api_key": "secret-value",
            "lease_token": "lease-secret",
            "headers": {"Authorization": "Bearer secret-value", "Accept": "application/json"},
        },
        "safe": "visible",
    }

    result = redact(data)

    assert result["authorization"] == "[REDACTED]"
    assert result["request"]["api_key"] == "[REDACTED]"
    assert result["request"]["lease_token"] == "[REDACTED]"
    assert result["request"]["headers"]["Authorization"] == "[REDACTED]"
    assert result["request"]["headers"]["Accept"] == "application/json"
    assert result["safe"] == "visible"


def test_redaction_drops_http_bodies_prompts_and_exception_messages() -> None:
    data = {
        "request_body": {"input": ["private course content"]},
        "response_body": {"answer": "private answer"},
        "prompt": "private prompt",
        "failure": RuntimeError("Bearer raw-secret in upstream exception"),
        "safe": "visible",
    }

    result = redact(data)

    assert result == {
        "request_body": "[REDACTED]",
        "response_body": "[REDACTED]",
        "prompt": "[REDACTED]",
        "failure": "[REDACTED]",
        "safe": "visible",
    }


def test_note_problem_codes_are_stable_and_actionable() -> None:
    expected = {
        "NOTE_ACTIVE_BATCH_LIMITED",
        "NOTE_BATCH_NOT_RETRYABLE",
        "NOTE_CITATION_INVALID",
        "NOTE_COVERAGE_INCOMPLETE",
        "NOTE_DOCUMENT_NOT_READY",
        "NOTE_EXPORT_CONTENT_UNSUPPORTED",
        "NOTE_EXPORT_RENDER_FAILED",
        "NOTE_EXPORT_REVOKED",
        "NOTE_EXPORT_UNAVAILABLE",
        "NOTE_OUTPUT_INVALID",
        "NOTE_PROVIDER_NOT_CONFIGURED",
        "NOTE_PROVIDER_TEMPORARILY_UNAVAILABLE",
        "NOTE_REQUEST_LIMIT_EXCEEDED",
        "NOTE_SOURCE_CHANGED",
        "NOTE_VERSION_NOT_FOUND",
    }

    assert {code.value for code in ProblemCode if code.name.startswith("NOTE_")} == expected


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("api_key=sample-value", id="api-key"),
        pytest.param("access_token=sample-value", id="access-token"),
        pytest.param("refresh_token=sample-value", id="refresh-token"),
        pytest.param("authorization=sample-value", id="authorization"),
        pytest.param("Bearer sample-value", id="bearer"),
        pytest.param("client_secret=sample-value", id="client-secret"),
        pytest.param("password=sample-value", id="password"),
        pytest.param("secret=sample-value", id="secret"),
        pytest.param("lease_token=sample-value", id="lease-token"),
        pytest.param("request_body=sample-payload", id="request-body"),
        pytest.param("response_body=sample-payload", id="response-body"),
        pytest.param("prompt=sample-input", id="prompt"),
        pytest.param("object_key=sample/object", id="object-key"),
        pytest.param("api key=sample-value", id="spaced-api-key"),
        pytest.param("access token=sample-value", id="spaced-access-token"),
        pytest.param("refresh token=sample-value", id="spaced-refresh-token"),
        pytest.param("client secret=sample-value", id="spaced-client-secret"),
        pytest.param("lease token=sample-value", id="spaced-lease-token"),
        pytest.param("request body=sample-payload", id="spaced-request-body"),
        pytest.param("response body=sample-payload", id="spaced-response-body"),
        pytest.param("object key=sample/object", id="spaced-object-key"),
        pytest.param("stack trace=sample-frame", id="spaced-stack-trace"),
        pytest.param("location=s3://sample-bucket/object", id="s3-uri"),
        pytest.param("location=file:///sample-root/object", id="file-uri"),
        pytest.param(
            "path=/synthetic-root-42/cache/sample.txt",
            id="arbitrary-unix-path",
        ),
        pytest.param(
            'path="/another-synthetic-root/sample.txt"',
            id="quoted-unix-path",
        ),
        pytest.param(
            "failed at /synthetic-space-root/cache/sample.txt",
            id="whitespace-unix-path",
        ),
        pytest.param(
            "path:/synthetic-colon-root/cache/sample.txt",
            id="path-colon-unix-path",
        ),
        pytest.param(r"path=Z:\synthetic-root\sample.docx", id="windows-drive-path"),
        pytest.param(
            r"path=\\synthetic-host\synthetic-share\sample.docx",
            id="unc-path",
        ),
        pytest.param("x" * 501, id="overlong"),
    ],
)
def test_sensitive_problem_details_are_sanitized_independently(detail: str) -> None:
    assert sanitize_problem_detail(detail) == GENERIC_PUBLIC_DETAIL


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("The secretariat is available.", id="secretariat"),
        pytest.param("The operation completed promptly.", id="promptly"),
    ],
)
def test_benign_marker_substrings_are_preserved(detail: str) -> None:
    assert sanitize_problem_detail(detail) == detail


@pytest.mark.parametrize(
    "detail",
    [
        pytest.param(
            "Request state is available at path=/api/v1/courses/course-1/notes.",
            id="api-route",
        ),
        pytest.param("Worker state is at path=/worker/v1/jobs/job-1", id="worker-route"),
        pytest.param("Health state is at path=/healthz", id="health-route"),
        pytest.param(
            "Provider status is at https://api.example.invalid/v1/status.",
            id="https-url",
        ),
    ],
)
def test_safe_public_api_path_is_preserved(detail: str) -> None:
    assert sanitize_problem_detail(detail) == detail


@pytest.mark.asyncio
async def test_problem_handler_projects_retry_metadata() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/capabilities",
            "raw_path": b"/api/v1/capabilities",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    response = await api_problem_handler(
        request,
        ApiProblem(
            status=503,
            code=ProblemCode.NOTE_PROVIDER_TEMPORARILY_UNAVAILABLE,
            title="笔记模型暂不可用",
            detail="Retry after the provider cooldown.",
            retryable=True,
            retry_after_ms=1_500,
        ),
    )

    body = json.loads(response.body)
    assert body["detail"] == "Retry after the provider cooldown."
    assert body["retryable"] is True
    assert body["retry_after_ms"] == 1_500
