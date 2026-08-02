from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.config import AppMode, Settings
from study_agent.identity.principal import (
    AuthenticationMethod,
    LocalPrincipalProvider,
    Principal,
)
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    NoteModel,
    NoteSourceModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.answering.source_tokens import LocalReadTokenSigner
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.courses.upload_validation import MAX_MARKDOWN_UPLOAD_BYTES
from study_agent.modules.sources import SourcePreviewService
from study_agent.storage.local import LocalStorage

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
FIXED_NOW = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
SIGNING_KEY = b"source-preview-test-signing-key!!"


class MutableClock:
    def now(self) -> datetime:
        return FIXED_NOW


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, client_host: str) -> Principal:
        del client_host
        return self._principal


@dataclass(frozen=True, slots=True)
class SeededPreview:
    course_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    note_id: str
    source_id: str
    object_key: str
    payload: bytes


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
    )


async def _seed_preview(
    database: Database,
    storage: LocalStorage,
    principal: Principal,
    *,
    filename: str,
    media_type: str,
    locator_kind: str,
    payload: bytes,
) -> SeededPreview:
    course = await CourseRepository(database).create(principal, "操作系统")
    note_id = "note-preview-1"
    source_id = "note-source-preview-1"
    async with database.session(principal) as session:
        seeded = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=("进程调度以就绪队列为输入。",),
            active=True,
            preview=False,
        )
        document = await session.get(DocumentModel, seeded.document_id)
        chunk = await session.get(RevisionChunkModel, seeded.chunk_ids[0])
        assert document is not None
        assert chunk is not None
        stored_object = await session.get(StoredObjectModel, document.stored_object_id)
        page = await session.scalar(
            select(RevisionPageModel).where(RevisionPageModel.revision_id == seeded.revision_id)
        )
        assert stored_object is not None
        assert page is not None

        document.filename = filename
        document.media_type = media_type
        stored_object.media_type = media_type
        stored_object.size_bytes = len(payload)
        chunk.locator_kind = locator_kind
        page.source_kind = locator_kind
        session.add(
            NoteModel(
                id=note_id,
                user_id=course.user_id,
                course_id=course.id,
                section_path=["进程管理"],
                title="进程管理",
                body_markdown="进程调度笔记",
                version=1,
                generation=1,
                generated_by_model=True,
                status="ready",
            )
        )
        await session.flush()
        session.add(
            NoteSourceModel(
                id=source_id,
                note_id=note_id,
                user_id=course.user_id,
                course_id=course.id,
                evidence_id="evidence-preview-1",
                document_id=document.id,
                revision_id=seeded.revision_id,
                chunk_id=chunk.id,
                document_name=filename,
                document_deletion_epoch=document.deletion_epoch,
                content_sha256=chunk.content_sha256,
                locator={"kind": locator_kind, "ordinal": chunk.page_ordinal},
                quote="进程调度以就绪队列为输入。",
                bounding_boxes=[],
                provenance=["native-test@1"],
                available=True,
            )
        )
        object_key = stored_object.object_key
    await storage.put_bytes(object_key, payload, media_type)
    return SeededPreview(
        course_id=course.id,
        document_id=seeded.document_id,
        revision_id=seeded.revision_id,
        chunk_id=seeded.chunk_ids[0],
        note_id=note_id,
        source_id=source_id,
        object_key=object_key,
        payload=payload,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("filename", "media_type", "locator_kind", "payload"),
    [
        ("lecture.pdf", "application/pdf", "page", b"%PDF-1.7 preview"),
        ("lecture.md", "text/markdown", "section", b"# Scheduling\n\nReady queue."),
        ("diagram.jpg", "image/jpeg", "page", b"\xff\xd8\xffpreview"),
        ("diagram.png", "image/png", "page", b"\x89PNG\r\n\x1a\npreview"),
    ],
)
async def test_note_and_graph_previews_stream_supported_originals(
    test_database_url: str,
    tmp_path: Path,
    filename: str,
    media_type: str,
    locator_kind: str,
    payload: bytes,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    clock = MutableClock()
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    seeded = await _seed_preview(
        database,
        storage,
        principal,
        filename=filename,
        media_type=media_type,
        locator_kind=locator_kind,
        payload=payload,
    )
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
        local_read_signer=LocalReadTokenSigner(SIGNING_KEY),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        note = await client.get(
            f"/api/v1/notes/{seeded.note_id}/sources/{seeded.source_id}/preview"
        )
        note_content = await client.get(note.json()["read_url"])
        graph = await client.get(
            f"/api/v1/courses/{seeded.course_id}/knowledge-graph/sources/"
            f"{seeded.revision_id}/{seeded.chunk_id}/preview"
        )
        graph_content = await client.get(graph.json()["read_url"])

        note_query = urlsplit(note.json()["read_url"]).query
        graph_content_path = urlsplit(graph.json()["read_url"]).path
        replayed = await client.get(f"{graph_content_path}?{note_query}")

    assert note.status_code == 200
    assert note.json()["source_id"] == seeded.source_id
    assert note.json()["document_name"] == filename
    assert note.json()["media_type"] == media_type
    assert note.json()["locator"]["kind"] == locator_kind
    assert note.json()["section_path"] == ["测试"]
    assert "object_key" not in note.json()
    assert note_content.status_code == 200
    assert note_content.content == payload
    assert note_content.headers["cache-control"] == "no-store"

    assert graph.status_code == 200
    assert graph.json()["source_id"] == seeded.chunk_id
    assert graph.json()["document_id"] == seeded.document_id
    assert graph.json()["media_type"] == media_type
    assert graph.json()["section_path"] == ["测试"]
    assert graph_content.status_code == 200
    assert graph_content.content == payload
    assert replayed.status_code == 404
    assert replayed.json()["code"] == "RESOURCE_NOT_FOUND"
    await database.dispose()


@pytest.mark.integration
async def test_source_previews_fail_closed_after_authorization_or_source_changes(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(
        subject="source-preview-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    outsider = Principal(
        subject="source-preview-outsider",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    clock = MutableClock()
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    seeded = await _seed_preview(
        database,
        storage,
        owner,
        filename="lecture.md",
        media_type="text/markdown",
        locator_kind="section",
        payload=b"# Scheduling\n\nReady queue.",
    )
    settings = _settings(test_database_url, tmp_path / "objects")
    owner_app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        clock=clock,
        principal_provider=StaticPrincipalProvider(owner),
    )
    outsider_app = create_app(
        settings=settings,
        database=database,
        storage=storage,
        clock=clock,
        principal_provider=StaticPrincipalProvider(outsider),
    )
    note_path = f"/api/v1/notes/{seeded.note_id}/sources/{seeded.source_id}/preview"
    graph_path = (
        f"/api/v1/courses/{seeded.course_id}/knowledge-graph/sources/"
        f"{seeded.revision_id}/{seeded.chunk_id}/preview"
    )

    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://testserver"
    ) as client:
        initial_note = await client.get(note_path)
        initial_graph = await client.get(graph_path)
    assert initial_note.status_code == 200
    assert initial_graph.status_code == 200
    old_note_content_url = initial_note.json()["read_url"]
    old_graph_content_url = initial_graph.json()["read_url"]

    async with AsyncClient(
        transport=ASGITransport(app=outsider_app), base_url="http://testserver"
    ) as client:
        hidden_note = await client.get(note_path)
        hidden_graph = await client.get(graph_path)
    assert hidden_note.status_code == 404
    assert hidden_graph.status_code == 404

    async with database.session(owner) as session:
        source = await session.get(NoteSourceModel, seeded.source_id)
        assert source is not None
        source.available = False
    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://testserver"
    ) as client:
        unavailable = await client.get(note_path)
        unavailable_old_content = await client.get(old_note_content_url)
    assert unavailable.status_code == 404
    assert unavailable_old_content.status_code == 404

    async with database.session(owner) as session:
        source = await session.get(NoteSourceModel, seeded.source_id)
        document = await session.get(DocumentModel, seeded.document_id)
        assert source is not None
        assert document is not None
        source.available = True
        document.review_status = "pending"
    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://testserver"
    ) as client:
        pending_note = await client.get(note_path)
        pending_graph = await client.get(graph_path)
        pending_old_graph_content = await client.get(old_graph_content_url)
    assert pending_note.status_code == 404
    assert pending_graph.status_code == 404
    assert pending_old_graph_content.status_code == 404

    async with database.session(owner) as session:
        document = await session.get(DocumentModel, seeded.document_id)
        assert document is not None
        document.review_status = "approved"
        document.deletion_epoch += 1
    async with AsyncClient(
        transport=ASGITransport(app=owner_app), base_url="http://testserver"
    ) as client:
        stale_epoch = await client.get(note_path)
    assert stale_epoch.status_code == 404
    await database.dispose()


@pytest.mark.integration
async def test_legacy_pptx_without_rendered_page_returns_stable_conflict(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    clock = MutableClock()
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    seeded = await _seed_preview(
        database,
        storage,
        principal,
        filename="legacy.pptx",
        media_type=PPTX_MEDIA_TYPE,
        locator_kind="slide",
        payload=b"PK\x03\x04legacy-presentation",
    )
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        note = await client.get(
            f"/api/v1/notes/{seeded.note_id}/sources/{seeded.source_id}/preview"
        )
        graph = await client.get(
            f"/api/v1/courses/{seeded.course_id}/knowledge-graph/sources/"
            f"{seeded.revision_id}/{seeded.chunk_id}/preview"
        )

    assert note.status_code == 409
    assert graph.status_code == 409
    assert note.json()["code"] == "INDEX_UNAVAILABLE"
    assert graph.json()["code"] == "INDEX_UNAVAILABLE"
    assert note.json()["title"] == "PPTX 预览页不可用"
    assert graph.json()["title"] == "PPTX 预览页不可用"
    await database.dispose()


@pytest.mark.integration
async def test_missing_original_source_does_not_report_a_pptx_error(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    clock = MutableClock()
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    seeded = await _seed_preview(
        database,
        storage,
        principal,
        filename="lecture.md",
        media_type="text/markdown",
        locator_kind="section",
        payload=b"# Scheduling\n\nReady queue.",
    )
    await storage.delete(seeded.object_key)
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            f"/api/v1/notes/{seeded.note_id}/sources/{seeded.source_id}/preview"
        )

    assert response.status_code == 409
    assert response.json()["title"] == "原始资料不可用"
    assert "PPTX" not in response.json()["detail"]
    await database.dispose()


@pytest.mark.integration
async def test_large_historical_markdown_is_not_sent_to_the_browser(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    clock = MutableClock()
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    seeded = await _seed_preview(
        database,
        storage,
        principal,
        filename="legacy-large.md",
        media_type="text/markdown",
        locator_kind="section",
        payload=b"# Scheduling\n\nReady queue.",
    )
    async with database.session(principal) as session:
        document = await session.get(DocumentModel, seeded.document_id)
        assert document is not None
        stored_object = await session.get(StoredObjectModel, document.stored_object_id)
        assert stored_object is not None
        stored_object.size_bytes = MAX_MARKDOWN_UPLOAD_BYTES + 1
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            f"/api/v1/notes/{seeded.note_id}/sources/{seeded.source_id}/preview"
        )

    assert response.status_code == 409
    assert response.json()["title"] == "Markdown 原文过大"
    assert "不超过 5 MB" in response.json()["detail"]
    await database.dispose()


@pytest.mark.integration
async def test_scoped_content_grant_is_checked_before_source_lookup(
    test_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(test_database_url)
    clock = MutableClock()
    signer = LocalReadTokenSigner(SIGNING_KEY)
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    calls: list[tuple[str, str]] = []

    async def unexpected_lookup(
        _service: SourcePreviewService,
        _principal: Principal,
        note_id: str,
        source_id: str,
    ) -> None:
        calls.append((note_id, source_id))
        return None

    monkeypatch.setattr(SourcePreviewService, "get_note_source", unexpected_lookup)
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
        local_read_signer=signer,
    )
    grant = signer.sign_scoped(
        "note-source",
        "other-note",
        "source-1",
        expires_at=datetime(2026, 7, 26, 8, 5, tzinfo=UTC),
    )
    base = "/api/v1/notes/note-1/sources/source-1/preview/content"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        missing = await client.get(base)
        replayed = await client.get(
            base,
            params={"expires": grant.expires, "signature": grant.signature},
        )

    assert missing.status_code == 404
    assert replayed.status_code == 404
    assert missing.json()["code"] == "RESOURCE_NOT_FOUND"
    assert replayed.json()["code"] == "RESOURCE_NOT_FOUND"
    assert calls == []
    await database.dispose()
