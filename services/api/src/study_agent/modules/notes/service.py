"""Evidence-backed note generation and optimistic editing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    CourseModel,
    DocumentModel,
    NoteModel,
    NoteSourceModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.models.core import new_id
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.retrieval import QueryEvidence, RetrievedEvidence
from study_agent.modules.answering.service import TrustedAnswerService
from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import Clock
from study_contracts import AnswerStatus, BoundingBox, SourceLocator, StructuredAnswer


class NoteVersionConflict(RuntimeError):
    def __init__(self, current_version: int) -> None:
        super().__init__("note version does not match If-Match")
        self.current_version = current_version


class NoteGenerationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class NoteSourceSnapshot:
    id: str
    evidence_id: str
    document_id: str
    revision_id: str
    chunk_id: str
    document_name: str
    locator: SourceLocator
    quote: str
    bounding_boxes: tuple[BoundingBox, ...]
    provenance: tuple[str, ...]
    available: bool
    stale: bool
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class NoteSnapshot:
    id: str
    course_id: str
    section_path: tuple[str, ...]
    title: str
    body_markdown: str
    version: int
    generation: int
    generated_by_model: bool
    status: str
    sources: tuple[NoteSourceSnapshot, ...]
    created_at: datetime
    updated_at: datetime


class NoteRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, principal: Principal, note_id: str) -> NoteSnapshot | None:
        async with self._database.session(principal) as session:
            note = await self._scoped(session, principal, note_id)
            return None if note is None else await self._snapshot(session, note)

    async def list_for_course(
        self,
        principal: Principal,
        course_id: str,
    ) -> tuple[NoteSnapshot, ...]:
        async with self._database.session(principal) as session:
            course = await self._course(session, principal, course_id)
            if course is None:
                raise LookupError("course is unavailable")
            notes = (
                await session.scalars(
                    select(NoteModel)
                    .where(
                        NoteModel.user_id == course.user_id,
                        NoteModel.course_id == course.id,
                    )
                    .order_by(NoteModel.updated_at.desc(), NoteModel.id)
                )
            ).all()
            return tuple([await self._snapshot(session, note) for note in notes])

    async def update(
        self,
        principal: Principal,
        note_id: str,
        *,
        expected_version: int,
        title: str | None,
        body_markdown: str | None,
    ) -> NoteSnapshot:
        if title is None and body_markdown is None:
            raise ValueError("at least one editable note field is required")
        async with self._database.session(principal) as session:
            note = await self._locked(session, principal, note_id)
            if note.version != expected_version:
                raise NoteVersionConflict(note.version)
            if title is not None:
                normalized_title = title.strip()
                if not normalized_title:
                    raise ValueError("note title must not be blank")
                note.title = normalized_title
            if body_markdown is not None:
                normalized_body = body_markdown.strip()
                if not normalized_body:
                    raise ValueError("note body must not be blank")
                note.body_markdown = normalized_body
            note.version += 1
            await session.flush()
            await session.refresh(note)
            return await self._snapshot(session, note)

    async def create_generated(
        self,
        principal: Principal,
        course_id: str,
        section_path: tuple[str, ...],
        title: str,
        answer: StructuredAnswer,
        retrieved: RetrievedEvidence,
    ) -> NoteSnapshot:
        if answer.status is not AnswerStatus.ANSWERED:
            raise NoteGenerationError("INSUFFICIENT_EVIDENCE")
        note_id = new_id()
        async with self._database.session(principal) as session:
            course = await self._course(session, principal, course_id)
            if course is None:
                raise LookupError("course is unavailable")
            if not await self._sources_current(session, course, retrieved):
                raise NoteGenerationError("SOURCE_CHANGED")
            note = NoteModel(
                id=note_id,
                user_id=course.user_id,
                course_id=course.id,
                section_path=list(section_path),
                title=title.strip(),
                body_markdown=answer.answer_markdown,
                version=1,
                generation=1,
                generated_by_model=True,
                status="ready",
            )
            session.add(note)
            await session.flush()
            self._replace_sources(session, note, answer, retrieved)
            await session.flush()
            await session.refresh(note)
            return await self._snapshot(session, note)

    async def regenerate(
        self,
        principal: Principal,
        note_id: str,
        *,
        expected_version: int,
        answer: StructuredAnswer,
        retrieved: RetrievedEvidence,
    ) -> NoteSnapshot:
        if answer.status is not AnswerStatus.ANSWERED:
            raise NoteGenerationError("INSUFFICIENT_EVIDENCE")
        async with self._database.session(principal) as session:
            note = await self._locked(session, principal, note_id)
            if note.version != expected_version:
                raise NoteVersionConflict(note.version)
            course = await session.get(CourseModel, note.course_id)
            if course is None or not await self._sources_current(session, course, retrieved):
                raise NoteGenerationError("SOURCE_CHANGED")
            await session.execute(delete(NoteSourceModel).where(NoteSourceModel.note_id == note.id))
            note.body_markdown = answer.answer_markdown
            note.generated_by_model = True
            note.status = "ready"
            note.failure_code = None
            note.version += 1
            note.generation += 1
            self._replace_sources(session, note, answer, retrieved)
            await session.flush()
            await session.refresh(note)
            return await self._snapshot(session, note)

    async def _course(
        self,
        session: AsyncSession,
        principal: Principal,
        course_id: str,
    ) -> CourseModel | None:
        return cast(
            CourseModel | None,
            await session.scalar(
                select(CourseModel)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    async def _scoped(
        self,
        session: AsyncSession,
        principal: Principal,
        note_id: str,
    ) -> NoteModel | None:
        return cast(
            NoteModel | None,
            await session.scalar(
                select(NoteModel)
                .join(CourseModel, CourseModel.id == NoteModel.course_id)
                .join(UserModel, UserModel.id == NoteModel.user_id)
                .where(
                    NoteModel.id == note_id,
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            ),
        )

    async def _locked(
        self,
        session: AsyncSession,
        principal: Principal,
        note_id: str,
    ) -> NoteModel:
        note = cast(
            NoteModel | None,
            await session.scalar(
                select(NoteModel)
                .join(UserModel, UserModel.id == NoteModel.user_id)
                .where(
                    NoteModel.id == note_id,
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
                .with_for_update(of=NoteModel)
            ),
        )
        if note is None:
            raise LookupError("note is unavailable")
        return note

    async def _snapshot(self, session: AsyncSession, note: NoteModel) -> NoteSnapshot:
        sources = (
            await session.scalars(
                select(NoteSourceModel)
                .where(NoteSourceModel.note_id == note.id)
                .order_by(NoteSourceModel.created_at, NoteSourceModel.id)
            )
        ).all()
        document_ids = {source.document_id for source in sources}
        documents = (
            (
                await session.scalars(
                    select(DocumentModel).where(DocumentModel.id.in_(document_ids))
                )
            ).all()
            if document_ids
            else []
        )
        by_document = {document.id: document for document in documents}
        source_snapshots: list[NoteSourceSnapshot] = []
        for source in sources:
            document = by_document.get(source.document_id)
            stale = document is not None and document.active_revision_id != source.revision_id
            current = (
                document is not None
                and document.deleted_at is None
                and document.deletion_epoch == source.document_deletion_epoch
                and document.active_revision_id == source.revision_id
            )
            available = source.available and current
            reason = source.unavailable_reason
            if not available and reason is None:
                reason = "SOURCE_REPLACED" if stale else "SOURCE_UNAVAILABLE"
            source_snapshots.append(
                NoteSourceSnapshot(
                    id=source.id,
                    evidence_id=source.evidence_id,
                    document_id=source.document_id,
                    revision_id=source.revision_id,
                    chunk_id=source.chunk_id,
                    document_name=source.document_name,
                    locator=SourceLocator.model_validate(source.locator),
                    quote=source.quote,
                    bounding_boxes=tuple(
                        BoundingBox.model_validate(item) for item in source.bounding_boxes
                    ),
                    provenance=tuple(source.provenance),
                    available=available,
                    stale=stale,
                    unavailable_reason=reason,
                )
            )
        return NoteSnapshot(
            id=note.id,
            course_id=note.course_id,
            section_path=tuple(note.section_path),
            title=note.title,
            body_markdown=note.body_markdown,
            version=note.version,
            generation=note.generation,
            generated_by_model=note.generated_by_model,
            status=note.status,
            sources=tuple(source_snapshots),
            created_at=note.created_at,
            updated_at=note.updated_at,
        )

    async def _sources_current(
        self,
        session: AsyncSession,
        course: CourseModel,
        retrieved: RetrievedEvidence,
    ) -> bool:
        if not retrieved.candidates:
            return False
        if course.active_lexical_index_id != retrieved.active_lexical_index_id:
            return False
        rows = (
            await session.execute(
                select(
                    RevisionChunkModel.id,
                    RevisionChunkModel.content_sha256,
                    DocumentModel.id.label("document_id"),
                    DocumentModel.active_revision_id,
                    DocumentModel.deletion_epoch,
                )
                .join(
                    DocumentModel,
                    and_(
                        DocumentModel.active_revision_id == RevisionChunkModel.revision_id,
                        DocumentModel.course_id == course.id,
                        DocumentModel.user_id == course.user_id,
                    ),
                )
                .where(
                    RevisionChunkModel.id.in_(
                        tuple(item.evidence.chunk_id for item in retrieved.candidates)
                    ),
                    DocumentModel.deleted_at.is_(None),
                )
                .with_for_update(of=DocumentModel)
            )
        ).all()
        by_chunk = {str(row.id): row for row in rows}
        return all(
            (row := by_chunk.get(item.evidence.chunk_id)) is not None
            and str(row.document_id) == item.evidence.document_id
            and str(row.active_revision_id) == item.evidence.revision_id
            and int(row.deletion_epoch) == item.document_deletion_epoch
            and str(row.content_sha256) == item.evidence.content_sha256
            for item in retrieved.candidates
        )

    @staticmethod
    def _replace_sources(
        session: AsyncSession,
        note: NoteModel,
        answer: StructuredAnswer,
        retrieved: RetrievedEvidence,
    ) -> None:
        by_id = {item.evidence.id: item for item in retrieved.candidates}
        for citation in answer.citations:
            source: AuthorizedEvidence = by_id[citation.id]
            session.add(
                NoteSourceModel(
                    id=new_id(),
                    note_id=note.id,
                    user_id=note.user_id,
                    course_id=note.course_id,
                    evidence_id=citation.id,
                    document_id=citation.document_id,
                    revision_id=citation.revision_id,
                    chunk_id=citation.chunk_id,
                    document_name=citation.document_name,
                    document_deletion_epoch=source.document_deletion_epoch,
                    content_sha256=source.evidence.content_sha256,
                    locator=citation.locator.model_dump(mode="json"),
                    quote=citation.quote,
                    bounding_boxes=[box.model_dump(mode="json") for box in citation.bounding_boxes],
                    provenance=list(source.provenance),
                    available=True,
                )
            )


class NoteService:
    def __init__(
        self,
        repository: NoteRepository,
        evidence: QueryEvidence,
        registry: ProviderRegistry,
        clock: Clock,
        *,
        timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._evidence = evidence
        self._clock = clock
        self._answering = TrustedAnswerService(
            registry.chat,
            timeout_seconds=timeout_seconds,
        )

    async def create(
        self,
        principal: Principal,
        course_id: str,
        section_path: tuple[str, ...],
        title: str,
    ) -> NoteSnapshot:
        question = self._generation_question(section_path, title)
        retrieved, answer = await self._generate(principal, course_id, question)
        return await self._repository.create_generated(
            principal,
            course_id,
            section_path,
            title,
            answer,
            retrieved,
        )

    async def regenerate(self, principal: Principal, note_id: str) -> NoteSnapshot:
        current = await self._repository.get(principal, note_id)
        if current is None:
            raise LookupError("note is unavailable")
        question = self._generation_question(current.section_path, current.title)
        retrieved, answer = await self._generate(principal, current.course_id, question)
        return await self._repository.regenerate(
            principal,
            note_id,
            expected_version=current.version,
            answer=answer,
            retrieved=retrieved,
        )

    async def _generate(
        self,
        principal: Principal,
        course_id: str,
        question: str,
    ) -> tuple[RetrievedEvidence, StructuredAnswer]:
        retrieved = await self._evidence.retrieve(
            principal,
            course_id,
            question,
            document_ids=None,
        )
        execution = await self._answering.answer(
            query_id=new_id(),
            question=question,
            active_index=retrieved.active_index,
            candidates=retrieved.candidates,
            sources_are_current=lambda: self._evidence.sources_are_current(
                principal,
                course_id,
                retrieved.active_lexical_index_id,
                retrieved.candidates,
            ),
        )
        if execution.answer is None:
            raise NoteGenerationError(execution.failure_code or "PROVIDER_BAD_RESPONSE")
        if execution.answer.status is not AnswerStatus.ANSWERED:
            assert execution.answer.refusal is not None
            raise NoteGenerationError(execution.answer.refusal.code)
        return retrieved, execution.answer

    @staticmethod
    def _generation_question(section_path: tuple[str, ...], title: str) -> str:
        path = " / ".join(section_path)
        return f"请仅依据课件证据生成章节笔记: {path} / {title.strip()}"
