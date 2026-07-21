from study_agent.api.errors import ProblemDetails
from study_agent.observability.redaction import redact


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
