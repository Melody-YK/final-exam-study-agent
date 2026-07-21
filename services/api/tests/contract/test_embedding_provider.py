from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from pydantic import SecretStr

from study_agent.providers.embedding_openai import OpenAICompatibleEmbeddingProvider
from study_agent.providers.errors import ProviderError, ProviderErrorCode

from ..fakes.provider_server import ScriptedProviderServer, ScriptedResponse


def embedding_response(*vectors: list[float], model: str = "BAAI/bge-m3") -> dict[str, object]:
    return {
        "object": "list",
        "model": model,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in reversed(tuple(enumerate(vectors)))
        ],
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }


async def make_provider(
    server: ScriptedProviderServer,
    *,
    batch_size: int = 64,
    max_attempts: int = 3,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    max_response_bytes: int = 8 * 1024 * 1024,
) -> tuple[OpenAICompatibleEmbeddingProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=server.transport)
    provider = OpenAICompatibleEmbeddingProvider(
        api_key=SecretStr("contract-secret"),
        base_url="http://embedding.test/v1",
        model="BAAI/bge-m3",
        batch_size=batch_size,
        timeout_seconds=0.01,
        max_attempts=max_attempts,
        retry_base_seconds=0.001,
        http_client=client,
        sleep=sleep,
        max_response_bytes=max_response_bytes,
    )
    return provider, client


@pytest.mark.asyncio
async def test_rejects_oversized_json_response_before_parsing() -> None:
    server = ScriptedProviderServer(ScriptedResponse(chunks=(b'{"data":"' + (b"x" * 256) + b'"}',)))
    provider, client = await make_provider(server, max_attempts=1, max_response_bytes=64)

    try:
        with pytest.raises(ProviderError) as captured:
            await provider.embed_query("query")
    finally:
        await client.aclose()

    assert captured.value.code is ProviderErrorCode.BAD_RESPONSE


@pytest.mark.asyncio
async def test_batches_inputs_and_restores_provider_index_order() -> None:
    server = ScriptedProviderServer(
        ScriptedResponse(json_body=embedding_response([1.0, 1.5], [2.0, 2.5])),
        ScriptedResponse(json_body=embedding_response([3.0, 3.5])),
    )
    provider, client = await make_provider(server, batch_size=2)

    try:
        vectors = await provider.embed_documents(["first", "second", "third"])
    finally:
        await client.aclose()

    assert vectors == [[1.0, 1.5], [2.0, 2.5], [3.0, 3.5]]
    assert [request.path for request in server.requests] == ["/v1/embeddings"] * 2
    assert server.requests[0].json_body == {
        "input": ["first", "second"],
        "model": "BAAI/bge-m3",
    }
    assert server.requests[0].headers["authorization"] == "Bearer contract-secret"
    server.assert_exhausted()


@pytest.mark.asyncio
async def test_probe_reports_actual_model_and_dimension_without_assuming_documentation() -> None:
    server = ScriptedProviderServer(
        ScriptedResponse(json_body=embedding_response([0.1, 0.2, 0.3], model="bge-m3-live"))
    )
    provider, client = await make_provider(server)

    try:
        contract = await provider.probe()
    finally:
        await client.aclose()

    assert contract.provider == "openai-compatible"
    assert contract.model == "bge-m3-live"
    assert contract.dimensions == 3
    assert contract.supports_batch is True
    assert server.requests[0].json_body == {
        "input": ["provider-contract-probe"],
        "model": "BAAI/bge-m3",
    }


@pytest.mark.asyncio
async def test_rejects_model_identity_drift_even_when_dimensions_match() -> None:
    server = ScriptedProviderServer(
        ScriptedResponse(json_body=embedding_response([0.1, 0.2], model="model-a")),
        ScriptedResponse(json_body=embedding_response([0.3, 0.4], model="model-b")),
    )
    provider, client = await make_provider(server, batch_size=1)

    try:
        with pytest.raises(ProviderError) as captured:
            await provider.embed_documents(["first", "second"])
    finally:
        await client.aclose()

    assert captured.value.code is ProviderErrorCode.EMBEDDING_MODEL_CHANGED


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_retries_rate_limits_and_server_errors(status_code: int) -> None:
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    server = ScriptedProviderServer(
        ScriptedResponse(
            status_code=status_code,
            headers={"Retry-After": "0.02"},
            json_body={"error": {"message": "private-upstream-detail"}},
        ),
        ScriptedResponse(json_body=embedding_response([0.1, 0.2])),
    )
    provider, client = await make_provider(server, sleep=record_sleep)

    try:
        vector = await provider.embed_query("query")
    finally:
        await client.aclose()

    assert vector == [0.1, 0.2]
    assert sleeps == [0.02]
    assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_retries_timeout_then_returns_normalized_safe_error() -> None:
    timeout = httpx.ReadTimeout("private timeout with contract-secret")
    server = ScriptedProviderServer(
        ScriptedResponse(exception=timeout),
        ScriptedResponse(exception=httpx.ReadTimeout("still private")),
    )
    provider, client = await make_provider(server, max_attempts=2, sleep=_no_sleep)

    try:
        with pytest.raises(ProviderError) as captured:
            await provider.embed_query("private query")
    finally:
        await client.aclose()

    assert captured.value.code is ProviderErrorCode.TIMEOUT
    assert captured.value.retryable is True
    rendered = f"{captured.value!r} {captured.value}"
    assert "contract-secret" not in rendered
    assert "private query" not in rendered
    assert "still private" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"data": "wrong"},
        {"data": [{"index": 0, "embedding": [1.0, "bad"]}]},
        {
            "data": [
                {"index": 0, "embedding": [1.0, 2.0]},
                {"index": 1, "embedding": [1.0]},
            ]
        },
    ],
)
async def test_rejects_invalid_or_dimension_drifting_bodies(body: object) -> None:
    server = ScriptedProviderServer(ScriptedResponse(json_body=body))
    provider, client = await make_provider(server, max_attempts=1)

    try:
        with pytest.raises(ProviderError) as captured:
            await provider.embed_documents(["one", "two"])
    finally:
        await client.aclose()

    assert captured.value.code in {
        ProviderErrorCode.BAD_RESPONSE,
        ProviderErrorCode.EMBEDDING_DIMENSION_CHANGED,
    }


async def _no_sleep(_delay: float) -> None:
    return None
