import pytest
from pydantic import SecretStr, ValidationError

from study_agent.config import AppMode, Settings


def test_local_settings_bind_loopback_and_leave_providers_unconfigured() -> None:
    settings = Settings(_env_file=None, app_mode=AppMode.LOCAL)

    assert settings.bind_host == "127.0.0.1"
    assert settings.embedding_api_key is None
    assert settings.deepseek_api_key is None
    assert settings.providers_configured is False


def test_production_settings_fail_closed_without_auth_and_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_mode=AppMode.PRODUCTION)


def test_production_settings_reject_weak_worker_token() -> None:
    with pytest.raises(ValidationError, match="worker token"):
        Settings(
            _env_file=None,
            app_mode=AppMode.PRODUCTION,
            auth_provider="oidc",
            auth_issuer="https://issuer.example",
            auth_audience="study-agent",
            allowed_hosts=("study.example",),
            allowed_origins=("https://study.example",),
            worker_token=SecretStr("x"),
        )


def test_production_settings_accept_strong_worker_token_and_explicit_origins() -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.PRODUCTION,
        auth_provider="oidc",
        auth_issuer="https://issuer.example",
        auth_audience="study-agent",
        allowed_hosts=("study.example",),
        allowed_origins=("https://study.example",),
        worker_token=SecretStr("correct-horse-battery-staple-8ca7"),
    )

    assert settings.effective_allowed_hosts == ("study.example",)
    assert settings.effective_allowed_origins == ("https://study.example",)


@pytest.mark.parametrize("allowed_hosts", [("*",), ("*.localhost",)])
def test_local_settings_reject_wildcard_allowed_hosts(
    allowed_hosts: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        Settings(
            _env_file=None,
            app_mode=AppMode.LOCAL,
            allowed_hosts=allowed_hosts,
        )


def test_production_settings_reject_global_host_wildcard() -> None:
    with pytest.raises(ValidationError, match="global wildcard"):
        Settings(
            _env_file=None,
            app_mode=AppMode.PRODUCTION,
            auth_provider="oidc",
            auth_issuer="https://issuer.example",
            auth_audience="study-agent",
            allowed_hosts=("*",),
            allowed_origins=("https://study.example",),
            worker_token=SecretStr("correct-horse-battery-staple-8ca7"),
        )


def test_production_settings_accept_restricted_dns_host_wildcard() -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.PRODUCTION,
        auth_provider="oidc",
        auth_issuer="https://issuer.example",
        auth_audience="study-agent",
        allowed_hosts=("*.study.example",),
        allowed_origins=("https://study.example",),
        worker_token=SecretStr("correct-horse-battery-staple-8ca7"),
    )

    assert settings.effective_allowed_hosts == ("*.study.example",)


def test_test_settings_allow_explicit_global_host_wildcard() -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        allowed_hosts=("*",),
    )

    assert settings.effective_allowed_hosts == ("*",)


def test_settings_are_immutable_after_validation() -> None:
    settings = Settings(_env_file=None, app_mode=AppMode.LOCAL)

    with pytest.raises(ValidationError, match="frozen"):
        settings.allowed_hosts = ("*",)

    assert settings.allowed_hosts == ()


def test_bracketed_ipv6_allowed_host_is_normalized() -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.LOCAL,
        bind_host="::1",
        allowed_hosts=("[::1]",),
    )

    assert settings.effective_allowed_hosts == ("::1",)


def test_runtime_provider_names_do_not_accept_fake() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_mode=AppMode.TEST, embedding_provider="fake")


def test_course_terms_are_normalized_for_shared_index_and_query_tokenization() -> None:
    settings = Settings(
        _env_file=None,
        course_terms=[" 虚拟内存 ", "缺页中断", "虚拟内存"],
    )

    assert settings.course_terms == ("缺页中断", "虚拟内存")


def test_note_workflow_is_disabled_by_default_and_exposes_safe_limits() -> None:
    settings = Settings(_env_file=None, app_mode=AppMode.TEST)

    assert settings.note_async_workflow_enabled is False
    assert settings.note_runner_enabled is False
    assert settings.note_numeric_eta_enabled is False
    assert settings.note_docx_export_enabled is False
    assert settings.note_export_runner_enabled is False
    assert settings.note_docx_renderer_enabled is False
    assert settings.note_workflow_configured is False
    assert settings.note_docx_configured is False
    assert settings.note_demo_phase_delay_seconds == 0.35
    assert settings.note_batch_max_documents > 0
    assert settings.note_batch_max_coverage_units >= settings.note_batch_max_documents


def _note_workflow_prerequisites() -> dict[str, object]:
    return {
        "app_mode": AppMode.TEST,
        "embedding_api_key": SecretStr("embedding-test-key"),
        "deepseek_api_key": SecretStr("chat-test-key"),
        "note_async_workflow_enabled": True,
        "note_runner_enabled": True,
    }


@pytest.mark.parametrize(
    ("missing_field", "expected_error"),
    [
        ("note_runner_enabled", "note_runner_enabled"),
        ("deepseek_api_key", "deepseek_api_key"),
        ("embedding_api_key", "embedding_api_key"),
    ],
)
def test_note_workflow_requires_each_capability_independently(
    missing_field: str,
    expected_error: str,
) -> None:
    values = _note_workflow_prerequisites()
    values.pop(missing_field)

    with pytest.raises(ValidationError, match=expected_error):
        Settings(_env_file=None, **values)


def test_note_workflow_accepts_a_fully_configured_capability_set() -> None:
    values = _note_workflow_prerequisites()
    values.update(
        note_docx_export_enabled=True,
        note_export_runner_enabled=True,
        note_docx_renderer_enabled=True,
        note_numeric_eta_enabled=True,
    )
    settings = Settings(_env_file=None, **values)

    assert settings.note_workflow_configured is True
    assert settings.note_docx_configured is True
    assert settings.note_docx_cjk_font_family == "Noto Sans CJK SC"


def test_note_coverage_unit_limit_must_cover_document_limit() -> None:
    with pytest.raises(ValidationError, match="coverage-unit limit"):
        Settings(
            _env_file=None,
            app_mode=AppMode.TEST,
            note_batch_max_documents=21,
            note_batch_max_coverage_units=20,
        )


@pytest.mark.parametrize("delay", [-0.01, 5.01])
def test_note_demo_phase_delay_is_bounded(delay: float) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_mode=AppMode.TEST,
            note_demo_phase_delay_seconds=delay,
        )


def test_note_dedup_retention_must_cover_event_retention() -> None:
    with pytest.raises(ValidationError, match="dedup retention"):
        Settings(
            _env_file=None,
            app_mode=AppMode.TEST,
            note_command_dedup_retention_seconds=10,
            note_event_retention_seconds=60,
        )


def test_note_numeric_eta_requires_the_async_workflow() -> None:
    with pytest.raises(ValidationError, match="numeric ETA"):
        Settings(
            _env_file=None,
            app_mode=AppMode.TEST,
            note_numeric_eta_enabled=True,
        )


@pytest.mark.parametrize(
    ("missing_field", "expected_error"),
    [
        ("note_async_workflow_enabled", "note_async_workflow_enabled"),
        ("note_export_runner_enabled", "note_export_runner_enabled"),
        ("note_docx_renderer_enabled", "note_docx_renderer_enabled"),
    ],
)
def test_note_docx_export_requires_each_prerequisite_independently(
    missing_field: str,
    expected_error: str,
) -> None:
    values = _note_workflow_prerequisites()
    values.update(
        note_docx_export_enabled=True,
        note_export_runner_enabled=True,
        note_docx_renderer_enabled=True,
    )
    values.pop(missing_field)

    with pytest.raises(ValidationError, match=expected_error):
        Settings(_env_file=None, **values)
