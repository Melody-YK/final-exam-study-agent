from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from study_agent.config import AppMode, Settings
from study_agent.providers.deepseek import DeepSeekChatProvider
from study_agent.providers.embedding_openai import OpenAICompatibleEmbeddingProvider
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import build_provider_registry
from study_agent.providers.probe import probe_registry
from study_agent.providers.vision import OpenAICompatibleVisionProvider

from ..fakes.provider_server import ScriptedProviderServer, ScriptedResponse


def configured_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "app_mode": AppMode.TEST,
        "embedding_base_url": "http://embedding.test/v1",
        "chat_base_url": "http://chat.test",
        "embedding_api_key": SecretStr("embedding-factory-secret"),
        "deepseek_api_key": SecretStr("chat-factory-secret"),
        "provider_retry_base_seconds": 0.001,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_registry_fails_closed_when_capability_is_unconfigured() -> None:
    registry = build_provider_registry(Settings(_env_file=None, app_mode=AppMode.TEST))

    try:
        with pytest.raises(ProviderError) as embedding_error:
            registry.embedding()
        with pytest.raises(ProviderError) as chat_error:
            registry.chat()
        with pytest.raises(ProviderError) as vision_error:
            registry.vision()
    finally:
        await registry.aclose()

    assert embedding_error.value.code is ProviderErrorCode.NOT_CONFIGURED
    assert chat_error.value.code is ProviderErrorCode.NOT_CONFIGURED
    assert vision_error.value.code is ProviderErrorCode.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_registry_contains_only_real_adapters_and_does_not_close_injected_client() -> None:
    server = ScriptedProviderServer()
    client = httpx.AsyncClient(transport=server.transport)
    registry = build_provider_registry(configured_settings(), http_client=client)

    assert isinstance(registry.embedding(), OpenAICompatibleEmbeddingProvider)
    assert isinstance(registry.chat(), DeepSeekChatProvider)
    assert "fake" not in repr(registry).lower()

    await registry.aclose()
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_registry_builds_optional_vision_adapter_on_the_shared_client() -> None:
    server = ScriptedProviderServer()
    client = httpx.AsyncClient(transport=server.transport)
    registry = build_provider_registry(
        configured_settings(
            vision_enabled=True,
            vision_api_key=SecretStr("vision-factory-secret"),
            vision_base_url="http://vision.test/v1",
        ),
        http_client=client,
    )

    assert isinstance(registry.vision(), OpenAICompatibleVisionProvider)
    assert registry.vision()._http is client
    assert "vision-factory-secret" not in repr(registry)

    await registry.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_registry_owns_one_shared_client_and_closes_it_idempotently() -> None:
    registry = build_provider_registry(configured_settings())
    embedding = registry.embedding()
    chat = registry.chat()
    assert isinstance(embedding, OpenAICompatibleEmbeddingProvider)
    assert isinstance(chat, DeepSeekChatProvider)
    assert embedding._http is chat._http
    owned_client = embedding._http

    await registry.aclose()
    await registry.aclose()

    assert owned_client.is_closed is True


def test_provider_settings_require_tls_when_a_secret_would_be_sent_outside_test() -> None:
    with pytest.raises(ValidationError):
        configured_settings(
            app_mode=AppMode.LOCAL,
            embedding_base_url="http://provider.example/v1",
        )


def test_provider_settings_repr_and_dump_do_not_reveal_secrets() -> None:
    settings = configured_settings()

    rendered = f"{settings!r} {settings.model_dump_json()}"

    assert "embedding-factory-secret" not in rendered
    assert "chat-factory-secret" not in rendered


@pytest.mark.asyncio
async def test_probe_report_contains_alias_capabilities_and_timing_but_no_private_data() -> None:
    embedding = {
        "model": "bge-m3-contract",
        "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
    }
    chat_payload = json.dumps({"status": "answered", "claims": []})
    chat = {
        "id": "probe-chat-id",
        "model": "deepseek-v4-flash",
        "choices": [{"message": {"content": chat_payload}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    server = ScriptedProviderServer(
        ScriptedResponse(json_body=embedding),
        ScriptedResponse(json_body=chat),
    )
    client = httpx.AsyncClient(transport=server.transport)
    registry = build_provider_registry(
        configured_settings(chat_stream=False),
        http_client=client,
    )

    try:
        report = await probe_registry(registry)
    finally:
        await registry.aclose()
        await client.aclose()

    serialized = report.to_json()
    assert report.status == "available"
    assert report.embedding is not None
    assert report.embedding.status == "available"
    assert report.embedding.endpoint_alias == "embedding-openai-compatible"
    assert report.embedding.dimensions == 3
    assert "batch" in report.embedding.capabilities
    assert report.chat is not None
    assert report.chat.status == "available"
    assert report.chat.endpoint_alias == "deepseek-chat"
    assert "json_output" in report.chat.capabilities
    assert "embedding-factory-secret" not in serialized
    assert "chat-factory-secret" not in serialized
    assert "provider-contract-probe" not in serialized
    assert "http://" not in serialized


@pytest.mark.asyncio
async def test_probe_reports_embedding_only_registry_as_partial_without_calling_chat() -> None:
    embedding = {
        "model": "bge-m3-contract",
        "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
    }
    server = ScriptedProviderServer(ScriptedResponse(json_body=embedding))
    client = httpx.AsyncClient(transport=server.transport)
    registry = build_provider_registry(
        configured_settings(deepseek_api_key=None),
        http_client=client,
    )

    try:
        report = await probe_registry(registry)
    finally:
        await registry.aclose()
        await client.aclose()

    assert report.status == "partial"
    assert report.embedding.status == "available"
    assert report.chat.status == "not_configured"
    assert report.chat.error_code == ProviderErrorCode.NOT_CONFIGURED.value
    assert [request.path for request in server.requests] == ["/v1/embeddings"]


@pytest.mark.parametrize(
    "run_live, rotated",
    [(None, None), ("1", None), (None, "1"), ("0", "1"), ("1", "0")],
)
def test_probe_cli_requires_both_live_safety_gates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_live: str | None,
    rotated: str | None,
) -> None:
    from study_agent.cli import main

    if run_live is None:
        monkeypatch.delenv("RUN_LIVE_PROVIDER_TESTS", raising=False)
    else:
        monkeypatch.setenv("RUN_LIVE_PROVIDER_TESTS", run_live)
    if rotated is None:
        monkeypatch.delenv("PROVIDER_CREDENTIALS_ROTATED", raising=False)
    else:
        monkeypatch.setenv("PROVIDER_CREDENTIALS_ROTATED", rotated)

    with pytest.raises(SystemExit) as captured:
        main()

    assert captured.value.code == 2
    output = capsys.readouterr().err
    assert "LIVE_PROVIDER_PROBE_DISABLED" in output
    assert "API_KEY" not in output


@pytest.mark.parametrize(
    "run_live, rotated, inject_secrets",
    [
        (None, None, True),
        ("1", None, True),
        (None, "1", True),
        ("0", "1", True),
        ("1", "0", True),
        ("1", "1", False),
    ],
)
def test_live_probe_script_exits_before_pytest_when_any_gate_or_secret_is_missing(
    run_live: str | None,
    rotated: str | None,
    inject_secrets: bool,
) -> None:
    workspace = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    for name in (
        "RUN_LIVE_PROVIDER_TESTS",
        "PROVIDER_CREDENTIALS_ROTATED",
        "EMBEDDING_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        environment.pop(name, None)
    if run_live is not None:
        environment["RUN_LIVE_PROVIDER_TESTS"] = run_live
    if rotated is not None:
        environment["PROVIDER_CREDENTIALS_ROTATED"] = rotated
    if inject_secrets:
        environment["EMBEDDING_API_KEY"] = "script-embedding-sentinel"
        environment["DEEPSEEK_API_KEY"] = "script-chat-sentinel"

    completed = subprocess.run(
        [str(workspace / "scripts/run_live_provider_probe.sh")],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    rendered = f"{completed.stdout}\n{completed.stderr}"
    assert "external-blocked" in rendered
    assert "script-embedding-sentinel" not in rendered
    assert "script-chat-sentinel" not in rendered
