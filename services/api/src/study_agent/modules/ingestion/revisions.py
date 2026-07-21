"""Validate parser artifacts and stage immutable preview revisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    PageCheckpointModel,
    ParseAttemptResultModel,
    ParseJobModel,
    RevisionAssetModel,
    RevisionBlockModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.modules.ingestion.chunker import chunk_pages
from study_agent.observability.trace import new_trace_id
from study_agent.providers.protocols import ObjectMetadata
from study_contracts import (
    PARSE_ATTEMPT_MEDIA_TYPE,
    PARSE_PAGE_MEDIA_TYPE,
    PARSER_RAW_MEDIA_TYPE,
    Asset,
    Chunk,
    Page,
    PageQualityStatus,
    ParseAttemptResult,
    ParseResultBundle,
    canonical_sha256,
)


class RevisionStorage(Protocol):
    async def head(self, object_key: str) -> ObjectMetadata: ...

    async def read_bytes(self, object_key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    revision_id: str | None
    missing_page_ordinals: tuple[int, ...]
    total_page_count: int

    @property
    def complete(self) -> bool:
        return self.revision_id is not None


@dataclass(frozen=True, slots=True)
class _VerifiedPage:
    page: Page
    attempt: int
    checkpoint: PageCheckpointModel


class RevisionService:
    """Build a preview only from artifacts verified inside the job scope."""

    def __init__(self, storage: RevisionStorage) -> None:
        self._storage = storage

    async def ingest_attempt(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        result_artifact: JobArtifactModel,
        *,
        reported_page_count: int,
        reported_failed_pages: list[int],
    ) -> IngestionOutcome:
        document = cast(
            DocumentModel | None,
            await session.scalar(
                select(DocumentModel)
                .where(
                    DocumentModel.id == job.document_id,
                    DocumentModel.course_id == job.course_id,
                    DocumentModel.user_id == job.user_id,
                    DocumentModel.deleted_at.is_(None),
                    DocumentModel.deletion_epoch == job.document_deletion_epoch,
                )
                .with_for_update(of=DocumentModel)
            ),
        )
        if document is None:
            raise self._state_conflict("文档在解析结果验证期间已失效。")

        payload = await self._read_verified_artifact(
            session,
            job,
            result_artifact,
            expected_media_type=PARSE_ATTEMPT_MEDIA_TYPE,
        )
        attempt = self._parse_attempt(payload)
        self._validate_attempt_claims(
            job,
            attempt,
            reported_page_count=reported_page_count,
            reported_failed_pages=reported_failed_pages,
        )
        await self._validate_attempt_pages(session, job, attempt)
        await self._validate_assets(
            session,
            job,
            attempt.assets,
            pages=attempt.pages,
            attempt=job.attempt,
        )

        persisted = await session.scalar(
            select(ParseAttemptResultModel)
            .where(
                ParseAttemptResultModel.job_id == job.id,
                ParseAttemptResultModel.attempt == job.attempt,
            )
            .with_for_update(of=ParseAttemptResultModel)
        )
        if persisted is None:
            persisted = ParseAttemptResultModel(
                id=new_trace_id(),
                job_id=job.id,
                user_id=job.user_id,
                course_id=job.course_id,
                document_id=job.document_id,
                artifact_id=result_artifact.id,
                attempt=job.attempt,
                document_sha256=attempt.document_sha256,
                schema_version=attempt.schema_version,
                parser_profile=attempt.parser_profile,
                source_backend=attempt.source_backend,
                source_version=attempt.source_version,
                total_page_count=attempt.total_page_count,
                requested_page_ordinals=list(attempt.requested_page_ordinals),
                covered_page_ordinals=list(attempt.covered_page_ordinals),
                canonical_sha256=attempt.canonical_sha256,
                payload=attempt.model_dump(mode="json"),
            )
            session.add(persisted)
            await session.flush()
        elif (
            persisted.artifact_id != result_artifact.id
            or persisted.canonical_sha256 != attempt.canonical_sha256
        ):
            raise self._state_conflict("相同 attempt 已持久化不同解析结果。")

        attempt_rows = list(
            await session.scalars(
                select(ParseAttemptResultModel)
                .where(ParseAttemptResultModel.job_id == job.id)
                .order_by(ParseAttemptResultModel.attempt)
            )
        )
        if any(row.total_page_count != attempt.total_page_count for row in attempt_rows):
            raise self._state_conflict("不同 attempt 声明了不一致的总页数。")

        verified_pages = await self._collect_verified_pages(
            session,
            job,
            total_page_count=attempt.total_page_count,
        )
        missing = tuple(
            ordinal
            for ordinal in range(1, attempt.total_page_count + 1)
            if ordinal not in verified_pages
        )
        if missing:
            return IngestionOutcome(
                revision_id=None,
                missing_page_ordinals=missing,
                total_page_count=attempt.total_page_count,
            )

        existing_revision_id = await session.scalar(
            select(DocumentRevisionModel.id).where(DocumentRevisionModel.parse_job_id == job.id)
        )
        if existing_revision_id is not None:
            return IngestionOutcome(
                revision_id=existing_revision_id,
                missing_page_ordinals=(),
                total_page_count=attempt.total_page_count,
            )

        pages = [verified_pages[ordinal].page for ordinal in range(1, attempt.total_page_count + 1)]
        page_provenance = {(page.source_backend, page.source_version) for page in pages}
        bundle_backend, bundle_version = (
            next(iter(page_provenance)) if len(page_provenance) == 1 else ("mixed", "mixed")
        )
        assets = await self._collect_revision_assets(
            session,
            job,
            attempt_rows,
            selected_attempts={
                ordinal: verified_page.attempt for ordinal, verified_page in verified_pages.items()
            },
        )
        bundle_payload = {
            "schema_version": "1.0",
            "document_sha256": job.document_sha256,
            "parser_profile": job.parser_profile,
            "source_backend": bundle_backend,
            "source_version": bundle_version,
            "pages": [page.model_dump(mode="json") for page in pages],
            "assets": [asset.model_dump(mode="json") for asset in assets],
        }
        try:
            bundle = ParseResultBundle.model_validate(
                {
                    **bundle_payload,
                    "canonical_sha256": canonical_sha256(bundle_payload),
                }
            )
        except ValidationError as exc:
            raise self._invalid_schema("跨 attempt 聚合结果不满足完整 bundle 契约。") from exc

        revision_id = new_trace_id()
        next_ordinal = (
            int(
                await session.scalar(
                    select(func.coalesce(func.max(DocumentRevisionModel.ordinal), 0)).where(
                        DocumentRevisionModel.document_id == document.id
                    )
                )
            )
            + 1
        )
        chunks = chunk_pages(bundle.pages, revision_id=revision_id)
        quality_status = (
            PageQualityStatus.WARNING.value
            if any(
                page.quality is not None and page.quality.status is PageQualityStatus.WARNING
                for page in bundle.pages
            )
            else PageQualityStatus.PASSED.value
        )
        revision = DocumentRevisionModel(
            id=revision_id,
            document_id=document.id,
            ordinal=next_ordinal,
            parse_job_id=job.id,
            manifest=bundle.model_dump(mode="json"),
            canonical_sha256=bundle.canonical_sha256,
            total_page_count=len(bundle.pages),
            parser_profile=bundle.parser_profile,
            parser_schema_version=bundle.schema_version,
            chunker_version="section-page-v1",
            quality_status=quality_status,
        )
        session.add(revision)
        await session.flush()
        self._persist_pages(session, revision_id, bundle.pages)
        await session.flush()
        self._persist_blocks_assets_chunks(session, revision_id, bundle.pages, assets, chunks)

        active_revision_id = document.active_revision_id
        document.preview_revision_id = revision_id
        document.status = "parsed_index_blocked"
        if document.active_revision_id != active_revision_id:
            raise RuntimeError("preview staging must not mutate active_revision_id")
        return IngestionOutcome(
            revision_id=revision_id,
            missing_page_ordinals=(),
            total_page_count=attempt.total_page_count,
        )

    def _validate_attempt_claims(
        self,
        job: ParseJobModel,
        attempt: ParseAttemptResult,
        *,
        reported_page_count: int,
        reported_failed_pages: list[int],
    ) -> None:
        if attempt.document_sha256 != job.document_sha256:
            raise self._hash_mismatch("attempt document hash 与 Job 不一致。")
        if attempt.parser_profile != job.parser_profile or attempt.schema_version != "1.0":
            raise self._invalid_schema("attempt parser profile 或 schema 不受支持。")
        native_backend = {
            "application/pdf": "pdf-native",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
                "pptx-native"
            ),
        }.get(job.media_type)
        backend_allowed = (
            attempt.source_backend == native_backend
            if job.parser_profile == "native-v1"
            else job.parser_profile == "ocr-v1"
            and attempt.source_backend in {"paddleocr-general", "pp-structure-v3"}
        )
        if not backend_allowed:
            raise self._invalid_schema("attempt backend 与文档媒体类型不一致。")
        expected_requested = list(job.requested_pages) or list(
            range(1, attempt.total_page_count + 1)
        )
        if attempt.requested_page_ordinals != expected_requested:
            raise self._state_conflict("attempt requested coverage 与当前租约不一致。")
        expected_failed = sorted(
            (set(attempt.requested_page_ordinals) - set(attempt.covered_page_ordinals))
            | {
                page.ordinal
                for page in attempt.pages
                if page.quality is not None and page.quality.status is PageQualityStatus.FAILED
            }
        )
        if reported_page_count != attempt.total_page_count:
            raise self._state_conflict("complete page_count 与 attempt total_page_count 不一致。")
        if sorted(reported_failed_pages) != expected_failed:
            raise self._state_conflict("complete failed_pages 与 attempt coverage 不一致。")
        if any(page.quality is None for page in attempt.pages):
            raise self._invalid_schema("covered pages 必须包含质量结果。")

    async def _validate_attempt_pages(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        attempt: ParseAttemptResult,
    ) -> None:
        checkpoints = (
            await session.scalars(
                select(PageCheckpointModel).where(
                    PageCheckpointModel.job_id == job.id,
                    PageCheckpointModel.attempt == job.attempt,
                )
            )
        ).all()
        by_ordinal = {checkpoint.page_ordinal: checkpoint for checkpoint in checkpoints}
        if sorted(by_ordinal) != attempt.covered_page_ordinals:
            raise self._state_conflict("attempt coverage 缺少对应的成功页检查点。")
        for page in attempt.pages:
            checkpoint = by_ordinal[page.ordinal]
            assert page.quality is not None
            expected_status = (
                "failed" if page.quality.status is PageQualityStatus.FAILED else "succeeded"
            )
            if checkpoint.status != expected_status:
                raise self._state_conflict("checkpoint status 与 Page quality 不一致。")
            if checkpoint.status == "failed" and checkpoint.error_code not in {
                issue.code for issue in page.quality.issues
            }:
                raise self._state_conflict("checkpoint error_code 与 Page quality issue 不一致。")
            verified = await self._read_checkpoint_page(session, job, checkpoint)
            if verified != page:
                raise self._hash_mismatch("attempt page 与 checkpoint page 内容不一致。")

    async def _collect_verified_pages(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        *,
        total_page_count: int,
    ) -> dict[int, _VerifiedPage]:
        checkpoints = (
            await session.scalars(
                select(PageCheckpointModel)
                .where(
                    PageCheckpointModel.job_id == job.id,
                    PageCheckpointModel.status == "succeeded",
                )
                .order_by(PageCheckpointModel.page_ordinal, PageCheckpointModel.attempt.desc())
            )
        ).all()
        selected: dict[int, _VerifiedPage] = {}
        for checkpoint in checkpoints:
            if checkpoint.page_ordinal > total_page_count or checkpoint.page_ordinal in selected:
                continue
            page = await self._read_checkpoint_page(session, job, checkpoint)
            if page.quality is None or page.quality.status is PageQualityStatus.FAILED:
                continue
            selected[checkpoint.page_ordinal] = _VerifiedPage(
                page=page,
                attempt=checkpoint.attempt,
                checkpoint=checkpoint,
            )
        return selected

    async def _read_checkpoint_page(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        checkpoint: PageCheckpointModel,
    ) -> Page:
        artifact = await self._artifact_for_ref(
            session,
            job,
            checkpoint.output_ref,
            attempt=checkpoint.attempt,
        )
        if artifact is None:
            raise self._hash_mismatch("checkpoint page artifact 不可用。")
        if (
            artifact.sha256 != checkpoint.output_sha256
            or artifact.size_bytes != checkpoint.output_size_bytes
            or artifact.artifact_schema_version != checkpoint.output_schema_version
        ):
            raise self._hash_mismatch("checkpoint page artifact 声明不一致。")
        payload = await self._read_verified_artifact(
            session,
            job,
            artifact,
            expected_media_type=PARSE_PAGE_MEDIA_TYPE,
        )
        try:
            page = Page.model_validate_json(payload)
        except ValidationError as exc:
            raise self._invalid_schema("checkpoint page schema 无效。") from exc
        if (
            page.ordinal != checkpoint.page_ordinal
            or page.source_backend != checkpoint.source_backend
            or page.source_version != checkpoint.source_version
        ):
            raise self._state_conflict("checkpoint page 定位或 parser provenance 不一致。")
        if any(block.raw_result_ref != page.raw_result_ref for block in page.blocks):
            raise self._state_conflict("Page 与 Block 必须引用同一 raw-page artifact。")
        raw_artifact = await self._artifact_for_ref(
            session,
            job,
            page.raw_result_ref,
            attempt=checkpoint.attempt,
        )
        if raw_artifact is None or raw_artifact.media_type != PARSER_RAW_MEDIA_TYPE:
            raise self._state_conflict("page raw_result_ref 不属于当前 job attempt。")
        await self._verify_artifact_metadata(session, raw_artifact)
        return page

    async def _collect_revision_assets(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        attempt_rows: list[ParseAttemptResultModel],
        *,
        selected_attempts: dict[int, int],
    ) -> list[Asset]:
        assets: list[Asset] = []
        for row in attempt_rows:
            try:
                attempt = ParseAttemptResult.model_validate(row.payload)
            except ValidationError as exc:
                raise self._invalid_schema("已持久化 attempt payload 无效。") from exc
            chosen = [
                asset
                for asset in attempt.assets
                if selected_attempts.get(asset.locator.ordinal) == row.attempt
            ]
            await self._validate_assets(
                session,
                job,
                chosen,
                pages=attempt.pages,
                attempt=row.attempt,
            )
            assets.extend(chosen)
        return assets

    async def _validate_assets(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        assets: list[Asset],
        *,
        pages: list[Page],
        attempt: int,
    ) -> None:
        pages_by_ordinal = {page.ordinal: page for page in pages}
        for asset in assets:
            page = pages_by_ordinal.get(asset.locator.ordinal)
            if (
                page is None
                or asset.locator.kind != page.source_kind
                or asset.source_backend != page.source_backend
                or asset.source_version != page.source_version
                or asset.raw_result_ref != page.raw_result_ref
            ):
                raise self._state_conflict("asset provenance 与所在页不一致。")
            object_artifact = await self._artifact_for_ref(
                session,
                job,
                asset.object_ref,
                attempt=attempt,
            )
            raw_artifact = await self._artifact_for_ref(
                session,
                job,
                asset.raw_result_ref,
                attempt=attempt,
            )
            if object_artifact is None or raw_artifact is None:
                raise self._state_conflict("asset 引用了当前 job attempt 之外的 artifact。")
            if (
                object_artifact.sha256 != asset.sha256
                or object_artifact.media_type != asset.media_type
                or (asset.size_bytes is not None and object_artifact.size_bytes != asset.size_bytes)
            ):
                raise self._hash_mismatch("asset object 声明与已上传 artifact 不一致。")
            await self._verify_artifact_metadata(session, object_artifact)
            await self._verify_artifact_metadata(session, raw_artifact)

    async def _artifact_for_ref(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        artifact_ref: str,
        *,
        attempt: int,
    ) -> JobArtifactModel | None:
        return cast(
            JobArtifactModel | None,
            await session.scalar(
                select(JobArtifactModel).where(
                    JobArtifactModel.id == artifact_ref,
                    JobArtifactModel.job_id == job.id,
                    JobArtifactModel.user_id == job.user_id,
                    JobArtifactModel.course_id == job.course_id,
                    JobArtifactModel.document_id == job.document_id,
                    JobArtifactModel.attempt == attempt,
                    JobArtifactModel.deletion_epoch == job.document_deletion_epoch,
                    JobArtifactModel.status == "available",
                )
            ),
        )

    async def _read_verified_artifact(
        self,
        session: AsyncSession,
        job: ParseJobModel,
        artifact: JobArtifactModel,
        *,
        expected_media_type: str,
    ) -> bytes:
        if artifact.media_type != expected_media_type:
            raise self._invalid_schema("artifact media type 与其解析角色不一致。")
        stored_object = await self._verify_artifact_metadata(session, artifact)
        try:
            payload = await self._storage.read_bytes(stored_object.object_key)
        except (FileNotFoundError, OSError) as exc:
            raise self._hash_mismatch("artifact object 不可读取。") from exc
        if len(payload) != artifact.size_bytes:
            raise self._hash_mismatch("artifact object 大小不一致。")
        if hashlib.sha256(payload).hexdigest() != artifact.sha256:
            raise self._hash_mismatch("artifact object hash 不一致。")
        if artifact.job_id != job.id:
            raise self._state_conflict("artifact 不属于当前 Job。")
        return payload

    async def _verify_artifact_metadata(
        self,
        session: AsyncSession,
        artifact: JobArtifactModel,
    ) -> StoredObjectModel:
        stored_object = await session.get(StoredObjectModel, artifact.stored_object_id)
        if (
            stored_object is None
            or stored_object.deleted_at is not None
            or stored_object.sha256 != artifact.sha256
            or stored_object.size_bytes != artifact.size_bytes
            or stored_object.media_type != artifact.media_type
        ):
            raise self._hash_mismatch("artifact persistence metadata 不一致。")
        try:
            metadata = await self._storage.head(stored_object.object_key)
        except (FileNotFoundError, OSError) as exc:
            raise self._hash_mismatch("artifact object 不可用。") from exc
        if (
            metadata.sha256 != artifact.sha256
            or metadata.size_bytes != artifact.size_bytes
            or metadata.content_type != artifact.media_type
        ):
            raise self._hash_mismatch("artifact object metadata 不一致。")
        return stored_object

    @staticmethod
    def _parse_attempt(payload: bytes) -> ParseAttemptResult:
        try:
            return ParseAttemptResult.model_validate_json(payload)
        except ValidationError as exc:
            if any(
                tuple(error.get("loc", ())) == ("canonical_sha256",)
                or "canonical_sha256" in str(error.get("msg", ""))
                for error in exc.errors()
            ):
                raise RevisionService._hash_mismatch("attempt canonical hash 无效。") from exc
            raise RevisionService._invalid_schema("attempt result schema 无效。") from exc

    @staticmethod
    def _persist_pages(session: AsyncSession, revision_id: str, pages: list[Page]) -> None:
        for page in pages:
            assert page.quality is not None
            session.add(
                RevisionPageModel(
                    id=new_trace_id(),
                    revision_id=revision_id,
                    page_ordinal=page.ordinal,
                    source_kind=page.source_kind,
                    width=page.width,
                    height=page.height,
                    bbox_norm=page.bbox_norm.model_dump(mode="json"),
                    source_backend=page.source_backend,
                    source_version=page.source_version,
                    raw_result_ref=page.raw_result_ref,
                    quality=page.quality.model_dump(mode="json"),
                )
            )

    @staticmethod
    def _persist_blocks_assets_chunks(
        session: AsyncSession,
        revision_id: str,
        pages: list[Page],
        assets: list[Asset],
        chunks: list[Chunk],
    ) -> None:
        for page in pages:
            for block in page.blocks:
                assert block.raw_result_ref is not None
                session.add(
                    RevisionBlockModel(
                        id=new_trace_id(),
                        revision_id=revision_id,
                        page_ordinal=page.ordinal,
                        block_id=block.id,
                        block_type=block.type.value,
                        text=block.text,
                        bbox_norm=block.bbox_norm.model_dump(mode="json"),
                        reading_order=block.reading_order,
                        confidence=block.confidence,
                        source_backend=block.source_backend,
                        source_version=block.source_version,
                        raw_result_ref=block.raw_result_ref,
                        parent_block_id=block.parent_id,
                        section_path=list(block.section_path),
                        metadata_json=dict(block.metadata),
                    )
                )
        for asset in assets:
            session.add(
                RevisionAssetModel(
                    id=new_trace_id(),
                    revision_id=revision_id,
                    asset_id=asset.id,
                    asset_type=asset.type.value,
                    locator_kind=asset.locator.kind,
                    page_ordinal=asset.locator.ordinal,
                    bbox_norm=asset.bbox_norm.model_dump(mode="json"),
                    object_ref=asset.object_ref,
                    media_type=asset.media_type,
                    sha256=asset.sha256,
                    source_backend=asset.source_backend,
                    source_version=asset.source_version,
                    raw_result_ref=asset.raw_result_ref,
                    size_bytes=asset.size_bytes,
                    metadata_json=dict(asset.metadata),
                )
            )
        for chunk in chunks:
            session.add(
                RevisionChunkModel(
                    id=chunk.id,
                    revision_id=revision_id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    locator_kind=chunk.locator.kind,
                    page_ordinal=chunk.locator.ordinal,
                    section_path=list(chunk.section_path),
                    source_block_ids=list(chunk.source_block_ids),
                    token_count_estimate=chunk.token_count_estimate,
                    content_sha256=chunk.content_sha256,
                    chunker_version=chunk.chunker_version,
                )
            )

    @staticmethod
    def _hash_mismatch(detail: str) -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.HASH_MISMATCH,
            title="解析 artifact 校验失败",
            detail=detail,
        )

    @staticmethod
    def _invalid_schema(detail: str) -> ApiProblem:
        return ApiProblem(
            status=422,
            code=ProblemCode.INVALID_REQUEST,
            title="解析结果 schema 无效",
            detail=detail,
        )

    @staticmethod
    def _state_conflict(detail: str) -> ApiProblem:
        return ApiProblem(
            status=409,
            code=ProblemCode.STATE_CONFLICT,
            title="解析结果状态冲突",
            detail=detail,
        )
