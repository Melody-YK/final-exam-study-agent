from pathlib import Path
from typing import Protocol

import pytest

from study_agent.providers.protocols import ObjectMetadata, ObjectScope, ObjectStorage
from study_agent.storage.local import LocalStorage

from ..fakes.storage import FakeObjectStorage


class WritableObjectStorage(ObjectStorage, Protocol):
    async def put_bytes(
        self, object_key: str, payload: bytes, content_type: str
    ) -> ObjectMetadata: ...


@pytest.mark.parametrize("backend", ["local", "fake-oss"])
async def test_object_storage_contract(
    backend: str,
    tmp_path: Path,
) -> None:
    storage: WritableObjectStorage = (
        LocalStorage(tmp_path) if backend == "local" else FakeObjectStorage()
    )

    target = await storage.create_upload(
        ObjectScope(subject="user", course_id="course", purpose="original")
    )
    written = await storage.put_bytes(target.object_key, b"content", "application/pdf")
    metadata = await storage.head(target.object_key)
    signed = await storage.sign_read(target.object_key)

    assert metadata == written
    assert metadata.size_bytes == 7
    assert metadata.sha256 is not None
    assert signed.url
    assert signed.expires_at.tzinfo is not None

    await storage.delete(target.object_key)
    await storage.delete(target.object_key)
    with pytest.raises(FileNotFoundError):
        await storage.head(target.object_key)
