from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from services.api.tests.integration.test_note_batch_demo import (
    _seed_ready_documents,
    _settings,
)
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import (
    NoteContentVersionModel,
    NoteGenerationBatchModel,
    NoteGenerationOutputModel,
    NoteModel,
    NoteSourceModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.notes.batch_service import NoteBatchService
from study_agent.modules.notes.demo_runner import DemoNoteRunner
from study_contracts import MergedNoteBatchRequest, NoteBatchCommandKind, NoteBatchStyle


@pytest.mark.integration
async def test_regeneration_runner_updates_same_note_and_recovers_pending_batch(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="note-regeneration-runner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    settings = _settings(test_database_url, tmp_path)
    service = NoteBatchService(database, settings, SystemClock())
    runner = DemoNoteRunner(database, settings, SystemClock(), phase_delay_seconds=0)
    try:
        course_id, document_ids = await _seed_ready_documents(database, principal)
        created = await service.create_batch(
            principal,
            course_id,
            MergedNoteBatchRequest(
                mode="merged",
                document_ids=list(document_ids),
                style=NoteBatchStyle.OUTLINE,
                section_path=["期末复习", "操作系统"],
                title="调度复习",
            ),
            "create-regeneration-target",
        )
        assert await runner.run_once(created.id) == created.id
        completed_create = await service.get_batch(principal, created.id)
        note_id = completed_create.items[0].note_id
        assert note_id is not None

        async with database.session(principal) as session:
            original = await session.get(NoteModel, note_id)
            original_version = await session.get(NoteContentVersionModel, (note_id, 1))
            original_source_ids = tuple(
                await session.scalars(
                    select(NoteSourceModel.id)
                    .where(NoteSourceModel.note_id == note_id)
                    .order_by(NoteSourceModel.id)
                )
            )
            assert original is not None
            assert original_version is not None
            assert original_source_ids
            original_body = original.body_markdown
            original_version_hash = original_version.note_version_sha256

        regeneration = await service.create_regeneration_batch(
            principal,
            note_id,
            1,
            "regenerate-note-v1",
        )
        assert regeneration.command_kind is NoteBatchCommandKind.REGENERATION
        assert regeneration.target_note_id == note_id
        assert regeneration.target_note_version == 1
        assert regeneration.target_note_version_sha256 == original_version_hash
        assert regeneration.style is NoteBatchStyle.OUTLINE
        assert [item.document_id for item in regeneration.inputs] == list(document_ids)

        assert await runner.recover_pending() == 1
        assert await runner.run_once(regeneration.id) == regeneration.id
        completed_regeneration = await service.get_batch(principal, regeneration.id)
        assert completed_regeneration.status.value == "succeeded"
        assert completed_regeneration.items[0].note_id == note_id

        async with database.session(principal) as session:
            regenerated = await session.get(NoteModel, note_id)
            versions = tuple(
                await session.scalars(
                    select(NoteContentVersionModel)
                    .where(NoteContentVersionModel.note_id == note_id)
                    .order_by(NoteContentVersionModel.version)
                )
            )
            outputs = tuple(
                await session.scalars(
                    select(NoteGenerationOutputModel)
                    .where(NoteGenerationOutputModel.note_id == note_id)
                    .order_by(NoteGenerationOutputModel.note_version)
                )
            )
            regenerated_source_ids = tuple(
                await session.scalars(
                    select(NoteSourceModel.id)
                    .where(NoteSourceModel.note_id == note_id)
                    .order_by(NoteSourceModel.id)
                )
            )
            note_count = await session.scalar(
                select(func.count()).select_from(NoteModel).where(NoteModel.id == note_id)
            )

            assert regenerated is not None
            assert regenerated.version == 2
            assert regenerated.generation == 2
            assert regenerated.title == "调度复习"
            assert regenerated.section_path == ["期末复习", "操作系统"]
            assert regenerated.body_markdown == original_body
            assert note_count == 1
            assert [version.version for version in versions] == [1, 2]
            assert versions[0].note_version_sha256 == original_version_hash
            assert versions[1].title == regenerated.title
            assert versions[1].section_path == regenerated.section_path
            assert [output.note_version for output in outputs] == [1, 2]
            assert {output.note_id for output in outputs} == {note_id}
            assert regenerated_source_ids
            assert regenerated_source_ids != original_source_ids
    finally:
        await runner.shutdown()
        await database.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("conflict_kind", ["note_version", "version_hash"])
async def test_regeneration_runner_fails_closed_when_target_changes(
    test_database_url: str,
    tmp_path: Path,
    conflict_kind: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject=f"note-regeneration-conflict-{conflict_kind}",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    settings = _settings(test_database_url, tmp_path)
    service = NoteBatchService(database, settings, SystemClock())
    runner = DemoNoteRunner(database, settings, SystemClock(), phase_delay_seconds=0)
    try:
        course_id, document_ids = await _seed_ready_documents(database, principal)
        created = await service.create_batch(
            principal,
            course_id,
            MergedNoteBatchRequest(mode="merged", document_ids=list(document_ids)),
            f"create-{conflict_kind}",
        )
        assert await runner.run_once(created.id) == created.id
        completed_create = await service.get_batch(principal, created.id)
        note_id = completed_create.items[0].note_id
        assert note_id is not None
        regeneration = await service.create_regeneration_batch(
            principal,
            note_id,
            1,
            f"regenerate-{conflict_kind}",
        )

        async with database.session(principal) as session:
            note = await session.get(NoteModel, note_id)
            assert note is not None
            original_body = note.body_markdown
            original_source_ids = tuple(
                await session.scalars(
                    select(NoteSourceModel.id)
                    .where(NoteSourceModel.note_id == note_id)
                    .order_by(NoteSourceModel.id)
                )
            )
            if conflict_kind == "note_version":
                note.version = 2
                note.body_markdown = "另一处已经保存的新内容"
                expected_body = note.body_markdown
            else:
                batch = await session.get(NoteGenerationBatchModel, regeneration.id)
                assert batch is not None
                batch.target_note_version_sha256 = "0" * 64
                expected_body = original_body

        assert await runner.run_once(regeneration.id) == regeneration.id
        failed = await service.get_batch(principal, regeneration.id)
        assert failed.status.value == "failed"
        assert failed.items[0].failure_code == "NOTE_VERSION_CONFLICT"
        assert failed.items[0].retryable_in_new_batch is True
        assert failed.items[0].note_id is None

        async with database.session(principal) as session:
            note = await session.get(NoteModel, note_id)
            current_source_ids = tuple(
                await session.scalars(
                    select(NoteSourceModel.id)
                    .where(NoteSourceModel.note_id == note_id)
                    .order_by(NoteSourceModel.id)
                )
            )
            version_count = await session.scalar(
                select(func.count())
                .select_from(NoteContentVersionModel)
                .where(NoteContentVersionModel.note_id == note_id)
            )
            output_count = await session.scalar(
                select(func.count())
                .select_from(NoteGenerationOutputModel)
                .where(NoteGenerationOutputModel.note_id == note_id)
            )

            assert note is not None
            assert note.body_markdown == expected_body
            assert current_source_ids == original_source_ids
            assert version_count == 1
            assert output_count == 1
    finally:
        await runner.shutdown()
        await database.dispose()
