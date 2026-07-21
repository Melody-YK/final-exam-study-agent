from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import LocalPrincipalProvider, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    AnswerDependencyModel,
    DocumentModel,
    DocumentRevisionModel,
    JobArtifactModel,
    ParseJobModel,
    QueryRunModel,
    RetrievalSnapshotModel,
    RevisionAssetModel,
    RevisionPageModel,
    StoredObjectModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.answering.source_tokens import LocalReadTokenSigner
from study_agent.modules.answering.sources import CitationSourceService
from study_agent.modules.courses.repository import CourseRepository
from study_agent.storage.local import LocalStorage

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PNG_MEDIA_TYPE = "image/png"
ORIGINAL_BYTES = b"PK\x03\x04original-presentation"
RENDERED_BYTES = b"\x89PNG\r\n\x1a\nrendered-slide-2"
STALE_RENDERED_BYTES = b"\x89PNG\r\n\x1a\nstale-slide-2"
FIXED_NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
SIGNING_KEY = b"source-token-test-key-material!!"


class MutableClock:
    def __init__(self, current: datetime = FIXED_NOW) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current


@dataclass(frozen=True, slots=True)
class SeededCitation:
    query_id: str
    citation_id: str
    rendered_object_key: str
    original_object_key: str


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
    )


def _content_url(
    signer: LocalReadTokenSigner,
    *,
    query_id: str,
    citation_id: str,
    expires_at: datetime,
) -> str:
    grant = signer.sign(query_id, citation_id, expires_at=expires_at)
    query = urlencode({"expires": grant.expires, "signature": grant.signature})
    return f"/api/v1/queries/{query_id}/citations/{citation_id}/content?{query}"


def _job(
    *,
    job_id: str,
    user_id: str,
    course_id: str,
    document_id: str,
    stored_object_id: str,
) -> ParseJobModel:
    return ParseJobModel(
        id=job_id,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        stored_object_id=stored_object_id,
        status="completed",
        state_version=1,
        attempt=1,
        max_attempts=3,
        parser_profile="native-v1",
        parser_schema_version="1.0",
        media_type=PPTX_MEDIA_TYPE,
        document_sha256="a" * 64,
        document_deletion_epoch=0,
        input_size_bytes=len(ORIGINAL_BYTES),
        estimated_pages=2,
        requires_ocr=False,
        requires_rendering=False,
        requested_pages=[],
        event_sequence=0,
        failed_pages=[],
    )


def _revision(
    *,
    revision_id: str,
    document_id: str,
    parse_job_id: str,
    ordinal: int,
) -> DocumentRevisionModel:
    return DocumentRevisionModel(
        id=revision_id,
        document_id=document_id,
        ordinal=ordinal,
        parse_job_id=parse_job_id,
        manifest={},
        canonical_sha256=f"{ordinal}" * 64,
        total_page_count=2,
        parser_profile="native-v1",
        parser_schema_version="1.0",
        chunker_version="section-page-v1",
        quality_status="passed",
    )


def _page(*, revision_id: str) -> RevisionPageModel:
    return RevisionPageModel(
        id=str(uuid4()),
        revision_id=revision_id,
        page_ordinal=2,
        source_kind="slide",
        width=1600,
        height=900,
        bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        source_backend="pptx-native",
        source_version="1",
        raw_result_ref="artifact://raw-slide-2",
        quality={"status": "passed"},
    )


def _artifact(
    *,
    artifact_id: str,
    job_id: str,
    user_id: str,
    course_id: str,
    document_id: str,
    stored_object_id: str,
    payload: bytes,
    status: str = "available",
) -> JobArtifactModel:
    return JobArtifactModel(
        id=artifact_id,
        job_id=job_id,
        user_id=user_id,
        course_id=course_id,
        document_id=document_id,
        stored_object_id=stored_object_id,
        attempt=1,
        deletion_epoch=0,
        artifact_name=f"{artifact_id}.png",
        artifact_schema_version="1.0",
        sha256="b" * 64,
        size_bytes=len(payload),
        media_type=PNG_MEDIA_TYPE,
        status=status,
    )


def _asset(
    *,
    asset_id: str,
    revision_id: str,
    artifact_id: str,
    payload: bytes,
) -> RevisionAssetModel:
    return RevisionAssetModel(
        id=str(uuid4()),
        revision_id=revision_id,
        asset_id=asset_id,
        asset_type="rendered_page",
        locator_kind="slide",
        page_ordinal=2,
        bbox_norm={"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
        object_ref=artifact_id,
        media_type=PNG_MEDIA_TYPE,
        sha256="b" * 64,
        source_backend="pptx-native",
        source_version="1",
        raw_result_ref="artifact://raw-slide-2",
        size_bytes=len(payload),
        metadata_json={},
    )


async def _seed_slide_citation(
    database: Database,
    storage: LocalStorage,
    principal: Principal,
    *,
    current_asset: str,
) -> SeededCitation:
    course = await CourseRepository(database).create(principal, "操作系统")
    document_id = str(uuid4())
    stale_revision_id = str(uuid4())
    active_revision_id = str(uuid4())
    stale_job_id = str(uuid4())
    active_job_id = str(uuid4())
    stale_artifact_id = str(uuid4())
    active_artifact_id = str(uuid4())
    query_id = str(uuid4())
    citation_id = f"{active_revision_id}:chunk:1"
    original_object_key = f"{principal.subject}/{course.id}/original/{document_id}.pptx"
    stale_object_key = f"{principal.subject}/{course.id}/artifact/stale-slide-2.png"
    rendered_object_key = f"{principal.subject}/{course.id}/artifact/active-slide-2.png"

    await storage.put_bytes(original_object_key, ORIGINAL_BYTES, PPTX_MEDIA_TYPE)
    await storage.put_bytes(stale_object_key, STALE_RENDERED_BYTES, PNG_MEDIA_TYPE)
    await storage.put_bytes(rendered_object_key, RENDERED_BYTES, PNG_MEDIA_TYPE)

    async with database.session(principal) as session:
        original_object = StoredObjectModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            object_key=original_object_key,
            purpose="original",
            sha256="a" * 64,
            size_bytes=len(ORIGINAL_BYTES),
            media_type=PPTX_MEDIA_TYPE,
        )
        session.add(original_object)
        await session.flush()
        document = DocumentModel(
            id=document_id,
            user_id=course.user_id,
            course_id=course.id,
            stored_object_id=original_object.id,
            filename="lecture.pptx",
            media_type=PPTX_MEDIA_TYPE,
            corpus_role="corpus",
            verified_sha256="a" * 64,
            status="ready",
            deletion_epoch=0,
        )
        session.add(document)
        await session.flush()

        stale_job = _job(
            job_id=stale_job_id,
            user_id=course.user_id,
            course_id=course.id,
            document_id=document.id,
            stored_object_id=original_object.id,
        )
        active_job = _job(
            job_id=active_job_id,
            user_id=course.user_id,
            course_id=course.id,
            document_id=document.id,
            stored_object_id=original_object.id,
        )
        session.add_all([stale_job, active_job])
        await session.flush()
        session.add_all(
            [
                _revision(
                    revision_id=stale_revision_id,
                    document_id=document.id,
                    parse_job_id=stale_job.id,
                    ordinal=1,
                ),
                _revision(
                    revision_id=active_revision_id,
                    document_id=document.id,
                    parse_job_id=active_job.id,
                    ordinal=2,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [_page(revision_id=stale_revision_id), _page(revision_id=active_revision_id)]
        )
        await session.flush()

        stale_object = StoredObjectModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            object_key=stale_object_key,
            purpose="job-artifact",
            sha256="b" * 64,
            size_bytes=len(STALE_RENDERED_BYTES),
            media_type=PNG_MEDIA_TYPE,
        )
        rendered_object = StoredObjectModel(
            id=str(uuid4()),
            user_id=course.user_id,
            course_id=course.id,
            object_key=rendered_object_key,
            purpose="job-artifact",
            sha256="b" * 64,
            size_bytes=len(RENDERED_BYTES),
            media_type=PNG_MEDIA_TYPE,
            deleted_at=datetime.now(UTC) if current_asset == "deleted_object" else None,
        )
        session.add_all([stale_object, rendered_object])
        await session.flush()
        stale_artifact = _artifact(
            artifact_id=stale_artifact_id,
            job_id=stale_job.id,
            user_id=course.user_id,
            course_id=course.id,
            document_id=document.id,
            stored_object_id=stale_object.id,
            payload=STALE_RENDERED_BYTES,
        )
        active_artifact = _artifact(
            artifact_id=active_artifact_id,
            job_id=active_job.id,
            user_id=course.user_id,
            course_id=course.id,
            document_id=document.id,
            stored_object_id=rendered_object.id,
            payload=RENDERED_BYTES,
            status="deleted" if current_asset == "deleted_artifact" else "available",
        )
        session.add_all([stale_artifact, active_artifact])
        await session.flush()
        session.add(
            _asset(
                asset_id="stale-rendered-slide-2",
                revision_id=stale_revision_id,
                artifact_id=stale_artifact.id,
                payload=STALE_RENDERED_BYTES,
            )
        )
        if current_asset != "missing":
            session.add(
                _asset(
                    asset_id="active-rendered-slide-2",
                    revision_id=active_revision_id,
                    artifact_id=(
                        stale_artifact.id if current_asset == "wrong_job" else active_artifact.id
                    ),
                    payload=(
                        STALE_RENDERED_BYTES if current_asset == "wrong_job" else RENDERED_BYTES
                    ),
                )
            )
        document.active_revision_id = active_revision_id

        query = QueryRunModel(
            id=query_id,
            user_id=course.user_id,
            course_id=course.id,
            question="第 2 页讲了什么?",
            question_sha256="c" * 64,
            requested_document_ids=[],
            status="answered",
            answer_schema_version="1.0",
            answer_markdown="虚拟内存提供地址空间隔离。",
            claims=[],
            citations=[],
            usage={},
            trace_id="source-test-trace",
            event_sequence=0,
        )
        session.add(query)
        await session.flush()
        snapshot = RetrievalSnapshotModel(
            id=str(uuid4()),
            query_id=query.id,
            user_id=course.user_id,
            course_id=course.id,
            active_revision_ids=[active_revision_id],
            document_epochs={document.id: 0},
            evidence_payload=[],
            candidate_count=1,
        )
        session.add(snapshot)
        await session.flush()
        session.add(
            AnswerDependencyModel(
                id=str(uuid4()),
                query_id=query.id,
                retrieval_snapshot_id=snapshot.id,
                user_id=course.user_id,
                course_id=course.id,
                evidence_id=citation_id,
                document_id=document.id,
                revision_id=active_revision_id,
                chunk_id=citation_id,
                document_name="lecture.pptx",
                document_deletion_epoch=0,
                content_sha256="d" * 64,
                locator={"kind": "slide", "ordinal": 2},
                quote="虚拟内存提供地址空间隔离。",
                bounding_boxes=[{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}],
                provenance=["pptx-native@1"],
                available=True,
            )
        )
    return SeededCitation(
        query_id=query_id,
        citation_id=citation_id,
        rendered_object_key=rendered_object_key,
        original_object_key=original_object_key,
    )


@pytest.mark.integration
async def test_slide_citation_selects_active_rendered_page_and_streams_it(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock()
    signer = LocalReadTokenSigner(SIGNING_KEY)
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    seeded = await _seed_slide_citation(
        database,
        storage,
        principal,
        current_asset="available",
    )
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
        local_read_signer=signer,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            f"/api/v1/queries/{seeded.query_id}/citations/{seeded.citation_id}"
        )
        content = await client.get(response.json()["read_url"])

    assert response.status_code == 200
    assert response.json()["media_type"] == PNG_MEDIA_TYPE
    read_url = urlsplit(response.json()["read_url"])
    read_query = parse_qs(read_url.query)
    expires_at = datetime.fromisoformat(
        response.json()["read_url_expires_at"].replace("Z", "+00:00")
    )
    assert read_url.path.endswith("/content")
    assert set(read_query) == {"expires", "signature"}
    assert expires_at == FIXED_NOW + timedelta(minutes=5)
    assert read_query["expires"] == [str(int(expires_at.timestamp()))]
    assert len(read_query["signature"][0]) == 43
    assert response.json()["locator"] == {"kind": "slide", "ordinal": 2}
    assert content.status_code == 200
    assert content.headers["content-type"] == PNG_MEDIA_TYPE
    assert content.content == RENDERED_BYTES
    assert content.content != ORIGINAL_BYTES
    assert await storage.read_bytes(seeded.original_object_key) == ORIGINAL_BYTES
    await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "current_asset",
    ["missing", "wrong_job", "deleted_artifact", "deleted_object", "missing_blob"],
)
async def test_slide_citation_fails_closed_when_rendered_page_is_unavailable(
    test_database_url: str,
    tmp_path: Path,
    current_asset: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    clock = MutableClock()
    signer = LocalReadTokenSigner(SIGNING_KEY)
    storage = LocalStorage(tmp_path / "objects", clock=clock)
    principal = LocalPrincipalProvider().resolve("127.0.0.1")
    seeded = await _seed_slide_citation(
        database,
        storage,
        principal,
        current_asset=current_asset,
    )
    if current_asset == "missing_blob":
        await storage.delete(seeded.rendered_object_key)
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
        local_read_signer=signer,
    )
    content_url = _content_url(
        signer,
        query_id=seeded.query_id,
        citation_id=seeded.citation_id,
        expires_at=clock.now() + timedelta(minutes=5),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            f"/api/v1/queries/{seeded.query_id}/citations/{seeded.citation_id}"
        )
        content = await client.get(content_url)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "INDEX_UNAVAILABLE"
    assert response.json()["title"] == "幻灯片预览资源不可用"
    assert content.status_code == 409
    assert content.json()["code"] == "INDEX_UNAVAILABLE"
    assert await storage.read_bytes(seeded.original_object_key) == ORIGINAL_BYTES
    await database.dispose()


@pytest.mark.integration
async def test_local_content_rejects_invalid_grants_before_source_lookup(
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
        _service: CitationSourceService,
        _principal: Principal,
        query_id: str,
        citation_id: str,
    ) -> None:
        calls.append((query_id, citation_id))
        return None

    monkeypatch.setattr(CitationSourceService, "get", unexpected_lookup)
    app = create_app(
        settings=_settings(test_database_url, tmp_path / "objects"),
        database=database,
        storage=storage,
        clock=clock,
        local_read_signer=signer,
    )
    future = signer.sign(
        "query-1",
        "citation-1",
        expires_at=clock.now() + timedelta(minutes=5),
    )
    expired = signer.sign(
        "query-1",
        "citation-1",
        expires_at=clock.now() - timedelta(seconds=1),
    )
    changed_signature = future.signature[:-1] + ("A" if future.signature[-1] != "A" else "B")
    base = "/api/v1/queries/query-1/citations/citation-1/content"
    attempts = [
        base,
        f"{base}?{urlencode({'expires': future.expires, 'signature': changed_signature})}",
        f"{base}?{urlencode({'expires': future.expires + 1, 'signature': future.signature})}",
        f"{base}?{urlencode({'expires': expired.expires, 'signature': expired.signature})}",
        _content_url(
            signer,
            query_id="query-2",
            citation_id="citation-1",
            expires_at=clock.now() + timedelta(minutes=5),
        ).replace("query-2", "query-1", 1),
        _content_url(
            signer,
            query_id="query-1",
            citation_id="citation-2",
            expires_at=clock.now() + timedelta(minutes=5),
        ).replace("citation-2", "citation-1", 1),
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        responses = [await client.get(url) for url in attempts]

    assert all(response.status_code == 404 for response in responses)
    assert all(response.json()["code"] == "RESOURCE_NOT_FOUND" for response in responses)
    assert calls == []
    await database.dispose()
