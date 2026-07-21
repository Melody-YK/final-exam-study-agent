from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

import httpx

_UNSET = object()


@dataclass(slots=True)
class ScriptedResponse:
    status_code: int = 200
    json_body: object = _UNSET
    headers: dict[str, str] = field(default_factory=dict)
    chunks: tuple[bytes, ...] | None = None
    interrupt_stream: bool = False
    exception: Exception | None = None


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    json_body: object


class _ScriptedByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...],
        *,
        request: httpx.Request,
        interrupt: bool,
    ) -> None:
        self._chunks = chunks
        self._request = request
        self._interrupt = interrupt

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            yield chunk
        if self._interrupt:
            raise httpx.ReadError("scripted stream interruption", request=self._request)

    async def aclose(self) -> None:
        return None


class ScriptedProviderServer:
    """In-process HTTP contract peer with deterministic response scripts."""

    def __init__(self, *responses: ScriptedResponse) -> None:
        self._responses = deque(responses)
        self.requests: list[RecordedRequest] = []
        self.transport = httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        raw_body = await request.aread()
        try:
            parsed_body: object = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            parsed_body = raw_body.decode("utf-8", errors="replace")
        self.requests.append(
            RecordedRequest(
                method=request.method,
                path=request.url.path,
                headers=dict(request.headers),
                json_body=parsed_body,
            )
        )
        if not self._responses:
            raise AssertionError(
                f"unexpected provider request: {request.method} {request.url.path}"
            )

        response = self._responses.popleft()
        if response.exception is not None:
            if isinstance(response.exception, httpx.RequestError):
                response.exception.request = request
            raise response.exception
        if response.chunks is not None:
            stream = _ScriptedByteStream(
                response.chunks,
                request=request,
                interrupt=response.interrupt_stream,
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=stream,
                request=request,
            )
        if response.json_body is not _UNSET:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                json=response.json_body,
                request=request,
            )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=b"not-json",
            request=request,
        )

    def assert_exhausted(self) -> None:
        assert not self._responses
