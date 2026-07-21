"""Exact media-type routing for the native parser profile."""

from __future__ import annotations

from typing import Protocol

from study_worker.parsers.normalize import RawDocument
from study_worker.parsers.protocols import ParserCapability, ParseRequest, ParserExecutionError


class NativeParserError(ParserExecutionError):
    """A stable parser failure that never contains document text or paths."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code, retryable=retryable)


class NativeParserAdapter(Protocol):
    @property
    def capability(self) -> ParserCapability: ...

    async def parse(self, request: ParseRequest) -> RawDocument: ...


class NativeParserRouter:
    def __init__(self, parsers: tuple[NativeParserAdapter, ...]) -> None:
        if not parsers:
            raise ValueError("native parser router requires at least one parser")
        routes: dict[str, NativeParserAdapter] = {}
        media_types: set[str] = set()
        for parser in parsers:
            if parser.capability.profile != "native-v1":
                raise ValueError("native router only accepts native-v1 parsers")
            for media_type in parser.capability.media_types:
                if media_type in routes:
                    raise ValueError(f"duplicate native media route: {media_type}")
                routes[media_type] = parser
                media_types.add(media_type)
        self._routes = routes
        self._capability = ParserCapability(
            profile="native-v1",
            source_backend="native-router",
            source_version="1.0",
            media_types=frozenset(media_types),
            supports_ocr=False,
            supports_rendering=False,
        )

    @property
    def capability(self) -> ParserCapability:
        return self._capability

    async def parse(self, request: ParseRequest) -> RawDocument:
        parser = self._routes.get(request.media_type)
        if parser is None:
            raise NativeParserError("UNSUPPORTED_MEDIA_TYPE")
        result = await parser.parse(request)
        if result.parser_profile != "native-v1":
            raise NativeParserError("PARSER_PROFILE_MISMATCH")
        return result
