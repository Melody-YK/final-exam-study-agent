import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from study_agent.providers.protocols import (
    ObjectMetadata,
    ObjectScope,
    SignedUrl,
    UploadTarget,
)


class FakeObjectStorage:
    """In-memory ObjectStorage contract fake; never registered at runtime."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    async def create_upload(self, scope: ObjectScope) -> UploadTarget:
        object_key = f"{scope.subject}/{scope.course_id}/{scope.purpose}/{uuid4().hex}"
        return UploadTarget(
            object_key=object_key,
            url=f"fake-upload://{object_key}",
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )

    async def put_bytes(self, object_key: str, payload: bytes, content_type: str) -> ObjectMetadata:
        self._objects[object_key] = (payload, content_type)
        return await self.head(object_key)

    async def head(self, object_key: str) -> ObjectMetadata:
        try:
            payload, content_type = self._objects[object_key]
        except KeyError as exc:
            raise FileNotFoundError(object_key) from exc
        digest = hashlib.sha256(payload).hexdigest()
        return ObjectMetadata(
            object_key=object_key,
            size_bytes=len(payload),
            content_type=content_type,
            sha256=digest,
            etag=digest,
        )

    async def sign_read(self, object_key: str) -> SignedUrl:
        await self.head(object_key)
        return SignedUrl(
            url=f"fake-read://{object_key}",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    async def delete(self, object_key: str) -> None:
        self._objects.pop(object_key, None)
