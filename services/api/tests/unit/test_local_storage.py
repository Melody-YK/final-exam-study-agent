import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from study_agent.providers.protocols import ObjectScope
from study_agent.storage.local import (
    LocalStorage,
    StorageBoundaryError,
    StorageUploadTooLarge,
)


async def test_local_storage_writes_and_reads_only_scoped_objects(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    target = await storage.create_upload(
        ObjectScope(subject="user-1", course_id="course-1", purpose="original")
    )

    await storage.put_bytes(target.object_key, b"course material", "application/pdf")
    metadata = await storage.head(target.object_key)
    payload = await storage.read_bytes(target.object_key)
    signed = await storage.sign_read(target.object_key)

    assert metadata.size_bytes == len(payload)
    assert metadata.sha256 is not None
    assert payload == b"course material"
    assert signed.url.startswith("local:///")


async def test_local_storage_forces_private_directory_and_file_modes(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    root.mkdir(mode=0o755)
    storage = LocalStorage(root)
    await storage.put_bytes("user/course/original/object", b"private", "application/pdf")

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "user").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "user/course/original/object").stat().st_mode) == 0o600


@pytest.mark.parametrize("object_key", ["../outside", "/tmp/outside", "course/../../outside"])
async def test_local_storage_rejects_path_traversal(tmp_path: Path, object_key: str) -> None:
    storage = LocalStorage(tmp_path)

    with pytest.raises(StorageBoundaryError):
        await storage.head(object_key)


async def test_local_storage_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"private")
    storage = LocalStorage(root)
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(StorageBoundaryError):
        await storage.head("linked/secret")


async def test_streaming_write_enforces_limit_without_partial_object(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    object_key = "user/course/original/object"

    async def chunks() -> AsyncIterator[bytes]:
        yield b"1234"
        yield b"5678"

    with pytest.raises(StorageUploadTooLarge):
        await storage.put_stream(
            object_key,
            chunks(),
            "application/pdf",
            max_bytes=7,
        )

    with pytest.raises(FileNotFoundError):
        await storage.head(object_key)
