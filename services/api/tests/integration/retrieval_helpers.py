from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)


@dataclass(frozen=True, slots=True)
class SeededRevision:
    document_id: str
    revision_id: str
    chunk_ids: tuple[str, ...]


async def seed_document_revision(
    session: AsyncSession,
    *,
    user_id: str,
    course_id: str,
    text_chunks: Sequence[str],
    active: bool,
    preview: bool,
    review_status: str = "approved",
    document_id: str | None = None,
    revision_ordinal: int = 1,
) -> SeededRevision:
    resolved_document_id = document_id or str(uuid4())
    revision_id = str(uuid4())
    if document_id is None:
        object_id = str(uuid4())
        digest = uuid4().hex * 2
        session.add(
            StoredObjectModel(
                id=object_id,
                user_id=user_id,
                course_id=course_id,
                object_key=f"tests/{course_id}/{resolved_document_id}",
                purpose="original",
                sha256=digest,
                size_bytes=100,
                media_type="application/pdf",
            )
        )
        await session.flush()
        session.add(
            DocumentModel(
                id=resolved_document_id,
                user_id=user_id,
                course_id=course_id,
                stored_object_id=object_id,
                filename=f"{resolved_document_id}.pdf",
                media_type="application/pdf",
                corpus_role="corpus",
                verified_sha256=digest,
                status="ready" if active and not preview else "parsed_index_blocked",
                review_status=review_status,
                deletion_epoch=0,
            )
        )
        await session.flush()
    revision = DocumentRevisionModel(
        id=revision_id,
        document_id=resolved_document_id,
        ordinal=revision_ordinal,
        manifest={},
        canonical_sha256=uuid4().hex * 2,
        total_page_count=1,
        parser_profile="native-v1",
        parser_schema_version="1.0",
        chunker_version="section-page-v1",
        quality_status="passed",
    )
    session.add(revision)
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
            raw_result_ref="artifact://test",
            quality={"status": "passed"},
        )
    )
    await session.flush()
    chunk_ids: list[str] = []
    for ordinal, chunk_text in enumerate(text_chunks, start=1):
        chunk_id = f"{revision_id}:chunk:{ordinal}"
        chunk_ids.append(chunk_id)
        session.add(
            RevisionChunkModel(
                id=chunk_id,
                revision_id=revision_id,
                ordinal=ordinal,
                text=chunk_text,
                locator_kind="page",
                page_ordinal=1,
                section_path=["测试"],
                source_block_ids=[f"block-{ordinal}"],
                token_count_estimate=max(1, len(chunk_text)),
                content_sha256=uuid4().hex * 2,
                chunker_version="section-page-v1",
            )
        )
    await session.flush()
    document = await session.get(DocumentModel, resolved_document_id)
    assert document is not None
    if active:
        document.active_revision_id = revision_id
    if preview:
        document.preview_revision_id = revision_id
    return SeededRevision(resolved_document_id, revision_id, tuple(chunk_ids))
