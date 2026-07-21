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
