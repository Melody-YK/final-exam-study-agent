from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    ChunkEmbeddingModel,
    ConversationModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    EmbeddingModelModel,
    LexicalManifestModel,
    NoteModel,
    NoteSourceModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.deletion.cleanup import DeletionCleanupService
from study_agent.storage.local import LocalStorage


@pytest.mark.integration
async def test_full_document_deletion_invalidates_sources_and_cleans_dependencies_idempotently(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path / "objects")
    lexical_root = tmp_path / "lexical"
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path / "objects",
        lexical_index_root=lexical_root,
    )
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    course = await CourseRepository(database).create(principal, "操作系统")
    document_id = str(uuid4())
    revision_id = str(uuid4())
    chunk_id = f"{revision_id}:chunk:1"
    object_id = str(uuid4())
    object_key = f"local-user/{course.id}/original/{document_id}"
    await storage.put_bytes(object_key, b"%PDF-1.7\nsource", "application/pdf")
    lexical_path = lexical_root / course.user_id / course.id / "manifest-v1"
    lexical_path.mkdir(parents=True)
    (lexical_path / "manifest.json").write_text("{}", encoding="utf-8")

    async with database.session(principal) as session:
        session.add(
            StoredObjectModel(
                id=object_id,
                user_id=course.user_id,
                course_id=course.id,
                object_key=object_key,
                purpose="original",
                sha256="a" * 64,
                size_bytes=16,
                media_type="application/pdf",
            )
        )
        await session.flush()
        document = DocumentModel(
            id=document_id,
            user_id=course.user_id,
            course_id=course.id,
            stored_object_id=object_id,
            filename="chapter.pdf",
            media_type="application/pdf",
            corpus_role="corpus",
            verified_sha256="a" * 64,
            status="ready",
            deletion_epoch=0,
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentRevisionModel(
                id=revision_id,
                document_id=document_id,
                ordinal=1,
                manifest={},
                canonical_sha256="b" * 64,
                total_page_count=1,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                chunker_version="section-page-v1",
                quality_status="passed",
            )
        )
        await session.flush()
        session.add(
            RevisionPageModel(
                id=str(uuid4()),
                revision_id=revision_id,
                page_ordinal=1,
                source_kind="page",
                width=1000,
                height=1000,
                bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                source_backend="pdf-native",
                source_version="1",
                raw_result_ref="artifact://page-1",
                quality={"status": "passed"},
            )
        )
        await session.flush()
        session.add(
            RevisionChunkModel(
                id=chunk_id,
                revision_id=revision_id,
                ordinal=1,
                text="进程是资源分配的基本单位。",
                locator_kind="page",
                page_ordinal=1,
                section_path=["进程"],
                source_block_ids=["block-1"],
                token_count_estimate=12,
                content_sha256="c" * 64,
                chunker_version="section-page-v1",
            )
        )
        model = EmbeddingModelModel(
            id=str(uuid4()),
            provider_alias="test",
            model_name="tiny",
            dimensions=2,
            distance_function="cosine",
            contract_version="1",
            status="active",
        )
        session.add(model)
        await session.flush()
        session.add(
            ChunkEmbeddingModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                embedding_model_id=model.id,
                dimensions=2,
                embedding=[1.0, 0.0],
            )
        )
        manifest = LexicalManifestModel(
            id="manifest-1",
            user_id=course.user_id,
            course_id=course.id,
            version_id="manifest-v1",
            storage_path=str(lexical_path),
            manifest_hash="d" * 64,
            document_set_hash="e" * 64,
            tokenizer_version="test-v1",
            dictionary_hash="f" * 64,
            chunk_count=1,
            document_ids=[document_id],
            revision_ids=[revision_id],
            status="active",
        )
        session.add(manifest)
        await session.flush()
        course_model = await session.get(CourseModel, course.id)
        assert course_model is not None
        course_model.active_lexical_index_id = manifest.id
        document.active_revision_id = revision_id

        conversation = ConversationModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            title="删除测试",
            auto_title_pending=False,
        )
        session.add(conversation)
        await session.flush()
        query = QueryRunModel(
            id="query-1",
            user_id=course.user_id,
            course_id=course.id,
            conversation_id=conversation.id,
            question="什么是进程?",
            question_sha256="0" * 64,
            requested_document_ids=[],
            status="answered",
            answer_schema_version="1.0",
            answer_markdown="进程是资源分配的基本单位。",
            claims=[{"id": "claim-1", "text": "定义", "citation_ids": [chunk_id]}],
            citations=[
                {
                    "id": chunk_id,
                    "document_id": document_id,
                    "revision_id": revision_id,
                    "chunk_id": chunk_id,
                    "document_name": "chapter.pdf",
                    "locator": {"kind": "page", "ordinal": 1},
                    "quote": "进程是资源分配的基本单位",
                    "bounding_boxes": [],
                }
            ],
            usage={},
            trace_id="query-trace-1",
            event_sequence=0,
        )
        session.add(query)
        await session.flush()
        snapshot = RetrievalSnapshotModel(
            id="snapshot-1",
            query_id=query.id,
            user_id=course.user_id,
            course_id=course.id,
            active_lexical_index_id=manifest.id,
            active_revision_ids=[revision_id],
            document_epochs={document_id: 0},
            evidence_payload=[
                {
                    "evidence": {
                        "document_id": document_id,
                        "revision_id": revision_id,
                        "chunk_id": chunk_id,
                        "text": "进程是资源分配的基本单位。",
                    }
                }
            ],
            candidate_count=1,
        )
        session.add(snapshot)
        await session.flush()
        session.add(
            AnswerDependencyModel(
                id="dependency-1",
                query_id=query.id,
                retrieval_snapshot_id=snapshot.id,
                user_id=course.user_id,
                course_id=course.id,
                evidence_id=chunk_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                document_name="chapter.pdf",
                document_deletion_epoch=0,
                content_sha256="c" * 64,
                locator={"kind": "page", "ordinal": 1},
                quote="进程是资源分配的基本单位",
                bounding_boxes=[],
                provenance=["pdf-native@1"],
                available=True,
            )
        )
        note = NoteModel(
            id="note-1",
            user_id=course.user_id,
            course_id=course.id,
            section_path=["进程"],
            title="进程",
            body_markdown="进程是资源分配的基本单位。",
            version=1,
            generation=1,
            generated_by_model=True,
            status="ready",
        )
        session.add(note)
        await session.flush()
        session.add(
            NoteSourceModel(
                id="note-source-1",
                note_id=note.id,
                user_id=course.user_id,
                course_id=course.id,
                evidence_id=chunk_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                document_name="chapter.pdf",
                document_deletion_epoch=0,
                content_sha256="c" * 64,
                locator={"kind": "page", "ordinal": 1},
                quote="进程是资源分配的基本单位",
                bounding_boxes=[],
                provenance=["pdf-native@1"],
                available=True,
            )
        )

    app = create_app(settings=settings, database=database, storage=storage)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        before = await client.get(f"/api/v1/queries/query-1/citations/{chunk_id}")
        content = await client.get(before.json()["read_url"])
        deleted = await client.delete(
            f"/api/v1/documents/{document_id}",
            headers={"Idempotency-Key": "full-delete-1"},
        )
        stale_content = await client.get(before.json()["read_url"])
        after = await client.get(f"/api/v1/queries/query-1/citations/{chunk_id}")
        query_response = await client.get("/api/v1/queries/query-1")
        note_response = await client.get("/api/v1/notes/note-1")

    assert before.status_code == 200
    assert "/content?" in before.json()["read_url"]
    assert content.status_code == 200
    assert content.content == b"%PDF-1.7\nsource"
    assert deleted.status_code == 202
    assert stale_content.status_code == 404
    assert after.status_code == 404
    assert query_response.json()["status"] == "invalidated"
    assert note_response.json()["sources"][0]["available"] is False
    assert not lexical_path.exists()
    with pytest.raises(FileNotFoundError):
        await storage.head(object_key)

    deletion_id = deleted.json()["deletion_id"]
    cleanup = DeletionCleanupService(database, lexical_root=lexical_root)
    await cleanup.cleanup(principal, deletion_id)
    await cleanup.cleanup(principal, deletion_id)
    async with database.session(principal) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DocumentRevisionModel)
                .where(DocumentRevisionModel.document_id == document_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ChunkEmbeddingModel)
                .where(ChunkEmbeddingModel.document_id == document_id)
            )
            == 0
        )
        dependency = await session.get(AnswerDependencyModel, "dependency-1")
        note_source = await session.get(NoteSourceModel, "note-source-1")
        persisted_snapshot = await session.get(RetrievalSnapshotModel, "snapshot-1")
        persisted_course = await session.get(CourseModel, course.id)
        assert dependency is not None and dependency.available is False
        assert dependency.quote == ""
        assert note_source is not None and note_source.available is False
        assert persisted_snapshot is not None and persisted_snapshot.evidence_payload == []
        assert persisted_course is not None and persisted_course.active_lexical_index_id is None
    await database.dispose()
