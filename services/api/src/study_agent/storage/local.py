"""Filesystem-backed object storage for local development."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterable, AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from uuid import uuid4

from study_agent.providers.protocols import (
    Clock,
    ObjectMetadata,
    ObjectScope,
    SignedUrl,
    UploadTarget,
)


class StorageBoundaryError(ValueError):
    """Raised when an object key resolves outside the configured root."""


class StorageUploadTooLarge(ValueError):
    """Raised while streaming an object beyond its configured byte limit."""


class LocalStorage:
    """A path-confined local implementation of the object storage contract."""

    def __init__(
        self,
        root: str | Path,
        *,
        upload_ttl: timedelta = timedelta(minutes=15),
        read_ttl: timedelta = timedelta(minutes=5),
        clock: Clock | None = None,
    ) -> None:
        root_path = Path(root).expanduser()
        root_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = root_path.resolve(strict=True)
        os.chmod(self._root, 0o700)
        self._upload_ttl = upload_ttl
        self._read_ttl = read_ttl
        self._clock = clock

    async def create_upload(self, scope: ObjectScope) -> UploadTarget:
        parts = tuple(
            self._scope_segment(value)
            for value in (
                scope.subject,
                scope.course_id,
                scope.purpose,
            )
        )
        object_key = PurePosixPath(*parts, uuid4().hex).as_posix()
        self._resolve(object_key)
        return UploadTarget(
            object_key=object_key,
            url=self._local_url(object_key),
            expires_at=self._now() + self._upload_ttl,
        )

    async def put_bytes(self, object_key: str, payload: bytes, content_type: str) -> ObjectMetadata:
        async def chunks() -> AsyncIterator[bytes]:
            yield payload

        return await self.put_stream(object_key, chunks(), content_type)

    async def put_stream(
        self,
        object_key: str,
        chunks: AsyncIterable[bytes],
        content_type: str,
        *,
        max_bytes: int | None = None,
    ) -> ObjectMetadata:
        """Atomically write an async byte stream without buffering the object."""

        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        path = self._resolve(object_key)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._assert_within_root(path.parent.resolve(strict=True), object_key)
        self._make_private(path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with os.fdopen(descriptor, "wb") as stream:
                async for block in chunks:
                    if not isinstance(block, bytes):
                        raise TypeError("storage chunks must be bytes")
                    if not block:
                        continue
                    size_bytes += len(block)
                    if max_bytes is not None and size_bytes > max_bytes:
                        raise StorageUploadTooLarge(
                            f"object exceeds the {max_bytes}-byte upload limit"
                        )
                    stream.write(block)
                    digest.update(block)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            os.chmod(path, 0o600)
        finally:
            temporary_path.unlink(missing_ok=True)

        self._atomic_write(
            self._metadata_path(path),
            json.dumps({"content_type": content_type}, separators=(",", ":")).encode(),
        )
        sha256 = digest.hexdigest()
        return ObjectMetadata(
            object_key=object_key,
            size_bytes=size_bytes,
            content_type=content_type,
            sha256=sha256,
            etag=sha256,
        )

    async def head(self, object_key: str) -> ObjectMetadata:
        path = self._resolve(object_key)
        if not path.is_file():
            raise FileNotFoundError(object_key)
        content_type = "application/octet-stream"
        metadata_path = self._metadata_path(path)
        if metadata_path.is_file():
            try:
                stored = json.loads(metadata_path.read_text(encoding="utf-8"))
                value = stored.get("content_type")
                if isinstance(value, str) and value:
                    content_type = value
            except (OSError, json.JSONDecodeError):
                pass
        return self._metadata(object_key, path, content_type)

    async def read_bytes(self, object_key: str) -> bytes:
        path = self._resolve(object_key)
        if not path.is_file():
            raise FileNotFoundError(object_key)
        return path.read_bytes()

    async def read_prefix(self, object_key: str, size: int = 16) -> bytes:
        if size <= 0:
            raise ValueError("prefix size must be positive")
        path = self._resolve(object_key)
        if not path.is_file():
            raise FileNotFoundError(object_key)
        with path.open("rb") as stream:
            return stream.read(size)

    async def stream_bytes(
        self, object_key: str, *, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        path = self._resolve(object_key)
        if not path.is_file():
            raise FileNotFoundError(object_key)
        with path.open("rb") as stream:
            while block := stream.read(chunk_size):
                yield block

    async def sign_read(self, object_key: str) -> SignedUrl:
        await self.head(object_key)
        return SignedUrl(
            url=self._local_url(object_key),
            expires_at=self._now() + self._read_ttl,
        )

    async def delete(self, object_key: str) -> None:
        path = self._resolve(object_key)
        if path.exists() and not path.is_file():
            raise StorageBoundaryError("object key does not identify a file")
        path.unlink(missing_ok=True)
        self._metadata_path(path).unlink(missing_ok=True)

    def _resolve(self, object_key: str) -> Path:
        if not object_key or "\\" in object_key or "\x00" in object_key:
            raise StorageBoundaryError("invalid object key")
        raw_parts = object_key.split("/")
        if any(part in {"", ".", ".."} for part in raw_parts):
            raise StorageBoundaryError("object key must be a normalized relative path")
        pure_path = PurePosixPath(object_key)
        if pure_path.is_absolute():
            raise StorageBoundaryError("absolute object keys are not allowed")
        resolved = (self._root / Path(*pure_path.parts)).resolve(strict=False)
        self._assert_within_root(resolved, object_key)
        return resolved

    def _assert_within_root(self, path: Path, object_key: str) -> None:
        try:
            path.relative_to(self._root)
        except ValueError as exc:
            raise StorageBoundaryError(f"object key escapes storage root: {object_key!r}") from exc
        if path == self._root:
            raise StorageBoundaryError("object key must identify an object")

    @staticmethod
    def _scope_segment(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise StorageBoundaryError("scope values must be non-empty path segments")
        return value

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.metadata.json")

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            os.chmod(path, 0o600)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _metadata(object_key: str, path: Path, content_type: str) -> ObjectMetadata:
        digest = hashlib.sha256()
        size_bytes = 0
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size_bytes += len(block)
                digest.update(block)
        sha256 = digest.hexdigest()
        return ObjectMetadata(
            object_key=object_key,
            size_bytes=size_bytes,
            content_type=content_type,
            sha256=sha256,
            etag=sha256,
        )

    def _make_private(self, directory: Path) -> None:
        current = directory
        while current != self._root:
            os.chmod(current, 0o700)
            current = current.parent
        os.chmod(self._root, 0o700)

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)

    @staticmethod
    def _local_url(object_key: str) -> str:
        return f"local:///{quote(object_key, safe='/')}"
