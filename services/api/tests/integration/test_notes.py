from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from study_agent.config import AppMode, Settings
from study_agent.identity.principal import CourseScope, LocalPrincipalProvider
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import NoteModel, NoteSourceModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.storage.local import LocalStorage


@pytest.mark.integration
async def test_note_if_match_edit_preserves_sources_and_exposes_conflict(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(test_database_url),
        local_storage_root=tmp_path,
        lexical_index_root=tmp_path / "lexical",
    )
    app = create_app(settings=settings, database=database, storage=LocalStorage(tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        course_response = await client.post("/api/v1/courses", json={"title": "编译原理"})
        course_id = course_response.json()["id"]
        principal = LocalPrincipalProvider().resolve("127.0.0.1")
        course = await CourseRepository(database).get(
            CourseScope(principal=principal, course_id=course_id)
        )
        assert course is not None
        async with database.session(principal) as session:
            session.add(
                NoteModel(
                    id="note-1",
                    user_id=course.user_id,
                    course_id=course.id,
                    section_path=["词法分析"],
                    title="词法分析",
                    body_markdown="原始笔记",
                    version=1,
                    generation=1,
                    generated_by_model=True,
                    status="ready",
                )
            )
            await session.flush()
            session.add(
                NoteSourceModel(
                    id="note-source-1",
                    note_id="note-1",
                    user_id=course.user_id,
                    course_id=course.id,
                    evidence_id="evidence-1",
                    document_id="deleted-document",
                    revision_id="revision-1",
                    chunk_id="chunk-1",
                    document_name="chapter.pdf",
                    document_deletion_epoch=0,
                    content_sha256="a" * 64,
                    locator={"kind": "page", "ordinal": 1},
                    quote="词法分析将字符流转换为记号流",
                    bounding_boxes=[],
                    provenance=["pdf-native@1"],
                    available=True,
                )
            )

        before = await client.get("/api/v1/notes/note-1")
        missing_precondition = await client.patch(
            "/api/v1/notes/note-1",
            json={"body_markdown": "缺少版本条件的覆盖"},
        )
        updated = await client.patch(
            "/api/v1/notes/note-1",
            headers={"If-Match": '"1"'},
            json={"body_markdown": "用户编辑后的笔记"},
        )
        conflict = await client.patch(
            "/api/v1/notes/note-1",
            headers={"If-Match": '"1"'},
            json={"body_markdown": "应被拒绝的覆盖"},
        )
        after = await client.get("/api/v1/notes/note-1")

    assert before.status_code == 200
    assert before.headers["etag"] == '"1"'
    assert before.json()["sources"][0]["id"] == "note-source-1"
    assert missing_precondition.status_code == 428
    assert missing_precondition.json()["code"] == "PRECONDITION_REQUIRED"
    assert missing_precondition.headers["content-type"].startswith("application/problem+json")
    assert updated.status_code == 200
    assert updated.headers["etag"] == '"2"'
    assert updated.json()["body_markdown"] == "用户编辑后的笔记"
    assert updated.json()["sources"][0]["id"] == "note-source-1"
    assert conflict.status_code == 412
    assert conflict.json()["code"] == "VERSION_CONFLICT"
    assert after.json()["version"] == 2
    assert after.json()["body_markdown"] == "用户编辑后的笔记"
    await database.dispose()
