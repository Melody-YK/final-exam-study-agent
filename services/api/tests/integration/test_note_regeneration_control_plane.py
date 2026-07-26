from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from services.api.tests.integration.test_note_batch_demo import (
    StaticPrincipalProvider,
    _provider_registry_without_upstream,
    _seed_ready_documents,
    _settings,
)
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    NoteContentVersionModel,
    NoteGenerationInputModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.notes.batch_service import NoteBatchService
from study_agent.modules.notes.demo_runner import DemoNoteRunner
from study_agent.storage.local import LocalStorage
from study_contracts import MergedNoteBatchRequest, NoteBatchStyle, NoteContentAstV1


class _RecordingRunner:
    def __init__(self) -> None:
        self.scheduled: list[str] = []

    def schedule(self, batch_id: str) -> None:
        self.scheduled.append(batch_id)


@pytest.mark.integration
async def test_workflow_note_edit_can_create_idempotent_regeneration_batch(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-regeneration-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    settings = _settings(test_database_url, tmp_path)
    course_id, document_ids = await _seed_ready_documents(database, principal)
    service = NoteBatchService(database, settings, SystemClock())
    source_batch = await service.create_batch(
        principal,
        course_id,
        MergedNoteBatchRequest(
            mode="merged",
            document_ids=list(document_ids),
            style=NoteBatchStyle.OUTLINE,
            title="原始标题",
            section_path=["原章节"],
        ),
        "shared-command-key",
    )
    runner = DemoNoteRunner(database, settings, SystemClock(), phase_delay_seconds=0)
    try:
        assert await runner.run_once(source_batch.id) == source_batch.id
        source_snapshot = await service.get_batch(principal, source_batch.id)
        note_id = source_snapshot.items[0].note_id
        assert note_id is not None

        app = create_app(
            settings=settings,
            database=database,
            storage=LocalStorage(tmp_path),
            principal_provider=StaticPrincipalProvider(principal),
            provider_registry=_provider_registry_without_upstream(),
        )
        recording_runner = _RecordingRunner()
        app.state.note_runner = recording_runner

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            original = await client.get(f"/api/v1/notes/{note_id}")
            assert original.status_code == 200
            assert original.headers["etag"] == '"1"'
            assert original.json()["origin_batch_id"] == source_batch.id

            edited = await client.patch(
                f"/api/v1/notes/{note_id}",
                headers={"If-Match": '"1"'},
                json={"title": "当前标题", "body_markdown": "用户编辑后的重点"},
            )
            assert edited.status_code == 200
            assert edited.headers["etag"] == '"2"'
            assert edited.json()["origin_batch_id"] == source_batch.id

            legacy_regeneration = await client.post(f"/api/v1/notes/{note_id}/regenerate")
            assert legacy_regeneration.status_code == 409
            assert legacy_regeneration.json()["code"] == "STATE_CONFLICT"
            unchanged = await client.get(f"/api/v1/notes/{note_id}")
            assert unchanged.headers["etag"] == '"2"'
            assert unchanged.json() == edited.json()

            missing_precondition = await client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={"Idempotency-Key": "missing-if-match"},
            )
            assert missing_precondition.status_code == 428
            assert missing_precondition.json()["code"] == "PRECONDITION_REQUIRED"
            assert missing_precondition.headers["content-type"].startswith(
                "application/problem+json"
            )

            stale = await client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={
                    "Idempotency-Key": "stale-regeneration",
                    "If-Match": '"1"',
                },
            )
            assert stale.status_code == 412
            assert stale.json()["code"] == "VERSION_CONFLICT"

            created = await client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={
                    "Idempotency-Key": "shared-command-key",
                    "If-Match": '"2"',
                },
            )
            assert created.status_code == 202
            regeneration = created.json()
            regeneration_id = regeneration["id"]
            assert created.headers["location"] == f"/api/v1/note-batches/{regeneration_id}"
            assert regeneration["command_kind"] == "regeneration"
            assert regeneration["target_note_id"] == note_id
            assert regeneration["target_note_version"] == 2
            assert regeneration["target_note_version_sha256"]
            assert regeneration["style"] == "outline"
            assert regeneration["title"] == "当前标题"
            assert regeneration["section_path"] == ["原章节"]
            assert [item["document_id"] for item in regeneration["inputs"]] == list(document_ids)
            assert recording_runner.scheduled == [regeneration_id]

            polled = await client.get(f"/api/v1/note-batches/{regeneration_id}")
            assert polled.status_code == 200
            assert polled.json()["command_kind"] == "regeneration"

            advanced = await client.patch(
                f"/api/v1/notes/{note_id}",
                headers={"If-Match": '"2"'},
                json={"body_markdown": "第二次用户编辑"},
            )
            assert advanced.status_code == 200
            assert advanced.headers["etag"] == '"3"'

            replay = await client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={
                    "Idempotency-Key": "shared-command-key",
                    "If-Match": '"2"',
                },
            )
            assert replay.status_code == 202
            assert replay.json()["id"] == regeneration_id

            changed_request = await client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={
                    "Idempotency-Key": "shared-command-key",
                    "If-Match": '"3"',
                },
            )
            assert changed_request.status_code == 409
            assert changed_request.json()["code"] == "IDEMPOTENCY_CONFLICT"

        outsider = Principal(
            subject="note-regeneration-outsider",
            authentication_method=AuthenticationMethod.LOCAL,
        )
        outsider_app = create_app(
            settings=settings,
            database=database,
            storage=LocalStorage(tmp_path),
            principal_provider=StaticPrincipalProvider(outsider),
            provider_registry=_provider_registry_without_upstream(),
        )
        outsider_runner = _RecordingRunner()
        outsider_app.state.note_runner = outsider_runner
        async with AsyncClient(
            transport=ASGITransport(app=outsider_app),
            base_url="http://testserver",
        ) as outsider_client:
            hidden = await outsider_client.post(
                f"/api/v1/notes/{note_id}/regeneration-batches",
                headers={
                    "Idempotency-Key": "outsider-regeneration",
                    "If-Match": '"3"',
                },
            )
        assert hidden.status_code == 404
        assert hidden.json()["code"] == "RESOURCE_NOT_FOUND"
        assert outsider_runner.scheduled == []

        async with database.session(principal) as session:
            first_version = await session.get(NoteContentVersionModel, (note_id, 1))
            edited_version = await session.get(NoteContentVersionModel, (note_id, 2))
            advanced_version = await session.get(NoteContentVersionModel, (note_id, 3))
            assert first_version is not None
            assert edited_version is not None
            assert advanced_version is not None
            assert edited_version.created_by == "user"
            assert edited_version.source_set_sha256 == first_version.source_set_sha256
            assert edited_version.coverage_manifest_sha256 == first_version.coverage_manifest_sha256
            NoteContentAstV1.model_validate(edited_version.content_ast)
            assert edited_version.content_ast["nodes"][0]["provenance"] == (
                "user_authored_unverified"
            )
            frozen_document_ids = tuple(
                await session.scalars(
                    select(NoteGenerationInputModel.document_id)
                    .where(NoteGenerationInputModel.batch_id == regeneration_id)
                    .order_by(NoteGenerationInputModel.ordinal)
                )
            )
            assert frozen_document_ids == document_ids
    finally:
        await runner.shutdown()
        await database.dispose()
