from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    LearningUnitModel,
    LearningUnitSourceModel,
    ParseJobModel,
    RevisionAssetModel,
    RevisionChunkModel,
    RevisionPageModel,
    StoredObjectModel,
    VisionReviewRunModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.providers.errors import ProviderError, ProviderErrorCode
from study_agent.providers.factory import ProviderRegistry
from study_agent.providers.protocols import (
    StructuredJsonDraft,
    VisionJsonCompletionPrompt,
)
from study_agent.storage.local import LocalStorage

OWNER = Principal(subject="vision-owner", authentication_method=AuthenticationMethod.LOCAL)
OUTSIDER = Principal(
    subject="vision-outsider",
    authentication_method=AuthenticationMethod.LOCAL,
)
PNG_BYTES = b"\x89PNG\r\n\x1a\nrendered-page"
JPG_BYTES = b"\xff\xd8\xff\xe0original-jpg"
PDF_BYTES = b"%PDF-1.7 original-pdf"


class _FixedPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, _client_host: str) -> Principal:
        return self._principal


class _VisionProvider:
    endpoint_alias = "test-vision-provider"
    model = "gpt-5.6-luna"

    def __init__(self, mode: str = "success") -> None:
        self.mode = mode
        self.calls: list[VisionJsonCompletionPrompt] = []

    async def complete_json(self, request: VisionJsonCompletionPrompt) -> StructuredJsonDraft:
        self.calls.append(request)
        if self.mode == "timeout":
            await asyncio.sleep(1)
        if self.mode == "invalid_json":
            raise ProviderError(
                ProviderErrorCode.BAD_RESPONSE,
                provider=self.endpoint_alias,
                retryable=False,
            )
        return StructuredJsonDraft(
            payload={
                "extracted_text": "某进程使用 2 个资源单元。",
                "question_type": "calculation",
                "conditions": ["已使用 2 个资源单元"],
                "reference_answer": "资源利用率为 25%。",
                "uncertain_spans": [],
                "evidence_complete": True,
                "confidence": "high",
                "reason": "页面内容清晰。",
            },
            model=self.model,
            provider_response_id="test-response-1",
            usage={"input_tokens": 23, "output_tokens": 11, "total_tokens": 34},
        )


@dataclass(frozen=True, slots=True)
class SeededSource:
    course_id: str
    unit_id: str
    source_id: str
    document_id: str
    revision_id: str


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _settings(database_url: str, root: Path, *, timeout: float = 0.1) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
        vision_enabled=True,
        vision_api_key=SecretStr("test-vision-secret"),
        vision_base_url="http://vision.test/v1",
        provider_timeout_seconds=timeout,
    )


def _provider_registry(provider: _VisionProvider) -> ProviderRegistry:
    return ProviderRegistry(
        embedding_provider=None,
        chat_provider=None,
        vision_provider=provider,
        http_client=None,
        owns_http_client=False,
    )


async def _seed_source(
    database: Database,
    storage: LocalStorage,
    principal: Principal,
    *,
    media_type: str,
    filename: str,
    rendered: bool,
) -> SeededSource:
    course = await CourseRepository(database).create(principal, "视觉复核测试")
    document_id = str(uuid4())
    revision_id = str(uuid4())
    unit_id = str(uuid4())
    source_id = str(uuid4())
    chunk_id = f"{revision_id}:chunk:1"
    original_bytes = PDF_BYTES if media_type == "application/pdf" else JPG_BYTES
    original_key = f"{principal.subject}/{course.id}/original/{document_id}"
    rendered_key = f"{principal.subject}/{course.id}/artifact/{document_id}.png"
    await storage.put_bytes(original_key, original_bytes, media_type)
    if rendered:
        await storage.put_bytes(rendered_key, PNG_BYTES, "image/png")

    original_hash = _sha256(original_bytes)
    rendered_hash = _sha256(PNG_BYTES)
    text = "某进程使用 2 个资源单元。"
    text_hash = _sha256(text)
    async with database.session(principal) as session:
        original_object = StoredObjectModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            object_key=original_key,
            purpose="original",
            sha256=original_hash,
            size_bytes=len(original_bytes),
            media_type=media_type,
        )
        session.add(original_object)
        await session.flush()
        document = DocumentModel(
            id=document_id,
            user_id=course.user_id,
            course_id=course.id,
            stored_object_id=original_object.id,
            filename=filename,
            media_type=media_type,
            corpus_role="corpus",
            verified_sha256=original_hash,
            status="ready",
            review_status="approved",
            deletion_epoch=0,
        )
        session.add(document)
        await session.flush()

        parse_job: ParseJobModel | None = None
        if rendered:
            parse_job = ParseJobModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                document_id=document_id,
                stored_object_id=original_object.id,
                status="completed",
                parser_profile="native-v1",
                parser_schema_version="1.0",
                media_type=media_type,
                document_sha256=original_hash,
                document_deletion_epoch=0,
                input_size_bytes=len(original_bytes),
                estimated_pages=1,
                requires_ocr=False,
                requires_rendering=True,
                requested_pages=[1],
                failed_pages=[],
            )
            session.add(parse_job)
            await session.flush()

        revision = DocumentRevisionModel(
            id=revision_id,
            document_id=document_id,
            parse_job_id=None if parse_job is None else parse_job.id,
            ordinal=1,
            manifest={},
            canonical_sha256="e" * 64,
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
                raw_result_ref="artifact://page-1",
                quality={"status": "passed"},
            )
        )
        await session.flush()
        chunk = RevisionChunkModel(
            id=chunk_id,
            revision_id=revision_id,
            ordinal=1,
            text=text,
            locator_kind="page",
            page_ordinal=1,
            section_path=["测试章节"],
            source_block_ids=["block-1"],
            token_count_estimate=6,
            content_sha256=text_hash,
            chunker_version="section-page-v1",
        )
        session.add(chunk)
        await session.flush()
        if rendered:
            rendered_object = StoredObjectModel(
                id=str(uuid4()),
                user_id=course.user_id,
                course_id=course.id,
                object_key=rendered_key,
                purpose="job-artifact",
                sha256=rendered_hash,
                size_bytes=len(PNG_BYTES),
                media_type="image/png",
            )
            session.add(rendered_object)
            await session.flush()
            artifact = JobArtifactModel(
                id=str(uuid4()),
                job_id=parse_job.id if parse_job is not None else "",
                user_id=course.user_id,
                course_id=course.id,
                document_id=document_id,
                stored_object_id=rendered_object.id,
                attempt=1,
                deletion_epoch=0,
                artifact_name="page-1.png",
                artifact_schema_version="1.0",
                sha256=rendered_hash,
                size_bytes=len(PNG_BYTES),
                media_type="image/png",
                status="available",
            )
            session.add(artifact)
            await session.flush()
            session.add(
                RevisionAssetModel(
                    id=str(uuid4()),
                    revision_id=revision_id,
                    asset_id="page-1",
                    asset_type="rendered_page",
                    locator_kind="page",
                    page_ordinal=1,
                    bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                    object_ref=artifact.id,
                    media_type="image/png",
                    sha256=rendered_hash,
                    source_backend="pdf-native",
                    source_version="1",
                    raw_result_ref="artifact://page-1",
                    size_bytes=len(PNG_BYTES),
                    metadata_json={},
                )
            )

        document.active_revision_id = revision_id
        session.add(
            LearningUnitModel(
                id=unit_id,
                user_id=course.user_id,
                course_id=course.id,
                canonical_key=f"section:{document_id}",
                label="测试章节",
                kind="section",
                status="available",
            )
        )
        session.add(
            LearningUnitSourceModel(
                id=source_id,
                user_id=course.user_id,
                course_id=course.id,
                unit_id=unit_id,
                document_id=document_id,
                revision_id=revision_id,
                chunk_id=chunk_id,
                content_sha256=text_hash,
                locator={"kind": "page", "ordinal": 1},
                status="valid",
            )
        )
    return SeededSource(course.id, unit_id, source_id, document_id, revision_id)


def _app(
    database: Database,
    storage: LocalStorage,
    settings: Settings,
    principal: Principal,
    provider: _VisionProvider,
):
    return create_app(
        settings=settings,
        database=database,
        storage=storage,
        principal_provider=_FixedPrincipalProvider(principal),
        provider_registry=_provider_registry(provider),
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_vision_review_api_renders_pdf_and_replays_idempotently(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    provider = _VisionProvider()
    seeded = await _seed_source(
        database,
        storage,
        OWNER,
        media_type="application/pdf",
        filename="review.pdf",
        rendered=True,
    )
    app = _app(database, storage, _settings(test_database_url, tmp_path), OWNER, provider)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            first = await client.post(
                f"/api/v1/courses/{seeded.course_id}/learning-units/{seeded.unit_id}/evidence/{seeded.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-pdf-1"},
            )
            replay = await client.post(
                f"/api/v1/courses/{seeded.course_id}/learning-units/{seeded.unit_id}/evidence/{seeded.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-pdf-1"},
            )
        async with database.session(OWNER) as session:
            runs = list(
                await session.scalars(
                    select(VisionReviewRunModel).where(
                        VisionReviewRunModel.source_id == seeded.source_id
                    )
                )
            )
    finally:
        await database.dispose()

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert len(provider.calls) == 1
    assert provider.calls[0].images[0].media_type == "image/png"
    assert provider.calls[0].images[0].data == PNG_BYTES
    assert len(runs) == 1
    assert runs[0].status == "succeeded"
    assert runs[0].provider == "test-vision-provider"
    assert runs[0].model == "gpt-5.6-luna"
    assert runs[0].usage == {"input_tokens": 23, "output_tokens": 11, "total_tokens": 34}
    assert runs[0].provider_response_id == "test-response-1"
    assert runs[0].source_content_sha256 == _sha256("某进程使用 2 个资源单元。")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_vision_review_api_uses_jpg_fallback_and_handles_missing_rendered_page(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    provider = _VisionProvider()
    jpg = await _seed_source(
        database,
        storage,
        OWNER,
        media_type="image/jpeg",
        filename="review.jpg",
        rendered=False,
    )
    missing = await _seed_source(
        database,
        storage,
        OWNER,
        media_type="application/pdf",
        filename="missing.pdf",
        rendered=False,
    )
    app = _app(database, storage, _settings(test_database_url, tmp_path), OWNER, provider)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            jpg_response = await client.post(
                f"/api/v1/courses/{jpg.course_id}/learning-units/{jpg.unit_id}/evidence/{jpg.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-jpg-1"},
            )
            missing_response = await client.post(
                f"/api/v1/courses/{missing.course_id}/learning-units/{missing.unit_id}/evidence/{missing.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-missing-1"},
            )
    finally:
        await database.dispose()

    assert jpg_response.status_code == 200
    assert provider.calls[0].images[0].media_type == "image/jpeg"
    assert provider.calls[0].images[0].data == JPG_BYTES
    assert missing_response.status_code == 409
    assert missing_response.json()["code"] == "INDEX_UNAVAILABLE"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_vision_review_api_is_principal_scoped_and_rejects_stale_revision(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    provider = _VisionProvider()
    seeded = await _seed_source(
        database,
        storage,
        OWNER,
        media_type="application/pdf",
        filename="private.pdf",
        rendered=True,
    )
    outsider_app = _app(
        database,
        storage,
        _settings(test_database_url, tmp_path),
        OUTSIDER,
        provider,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=outsider_app),
            base_url="http://testserver",
        ) as client:
            forbidden = await client.post(
                f"/api/v1/courses/{seeded.course_id}/learning-units/{seeded.unit_id}/evidence/{seeded.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-outsider-1"},
            )
        async with database.session(OWNER) as session:
            document = await session.get(DocumentModel, seeded.document_id)
            assert document is not None
            replacement_revision = DocumentRevisionModel(
                id=str(uuid4()),
                document_id=seeded.document_id,
                ordinal=2,
                manifest={},
                canonical_sha256="f" * 64,
                total_page_count=1,
                parser_profile="native-v1",
                parser_schema_version="1.0",
                chunker_version="section-page-v1",
                quality_status="passed",
            )
            session.add(replacement_revision)
            await session.flush()
            document.active_revision_id = replacement_revision.id
        owner_app = _app(database, storage, _settings(test_database_url, tmp_path), OWNER, provider)
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://testserver",
        ) as client:
            stale = await client.post(
                f"/api/v1/courses/{seeded.course_id}/learning-units/{seeded.unit_id}/evidence/{seeded.source_id}/vision-review",
                headers={"Idempotency-Key": "vision-stale-1"},
            )
    finally:
        await database.dispose()

    assert forbidden.status_code == 404
    assert stale.status_code == 409
    assert len(provider.calls) == 0


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "provider_timeout", "expected_status", "expected_code"),
    [
        ("timeout", 0.01, 504, "PROVIDER_TIMEOUT"),
        ("invalid_json", 0.1, 502, "PROVIDER_BAD_RESPONSE"),
    ],
)
async def test_vision_review_api_audits_timeout_and_invalid_json(
    test_database_url: str,
    tmp_path: Path,
    mode: str,
    provider_timeout: float,
    expected_status: int,
    expected_code: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    storage = LocalStorage(tmp_path)
    provider = _VisionProvider(mode)
    seeded = await _seed_source(
        database,
        storage,
        OWNER,
        media_type="application/pdf",
        filename=f"{mode}.pdf",
        rendered=True,
    )
    app = _app(
        database,
        storage,
        _settings(test_database_url, tmp_path, timeout=provider_timeout),
        OWNER,
        provider,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                f"/api/v1/courses/{seeded.course_id}/learning-units/{seeded.unit_id}/evidence/{seeded.source_id}/vision-review",
                headers={"Idempotency-Key": f"vision-{mode}-1"},
            )
        async with database.session(OWNER) as session:
            run = await session.scalar(
                select(VisionReviewRunModel)
                .where(VisionReviewRunModel.source_id == seeded.source_id)
                .order_by(VisionReviewRunModel.created_at.desc())
                .limit(1)
            )
    finally:
        await database.dispose()

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == expected_code
