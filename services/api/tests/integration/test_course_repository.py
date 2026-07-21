import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from study_agent.identity.principal import AuthenticationMethod, CourseScope, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import DocumentModel, StoredObjectModel
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.repository import CourseRepository


@pytest.mark.integration
async def test_course_repository_enforces_principal_scope(test_database_url: str) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    repository = CourseRepository(database)
    owner = Principal(subject="owner", authentication_method=AuthenticationMethod.LOCAL)
    stranger = Principal(subject="stranger", authentication_method=AuthenticationMethod.LOCAL)

    course = await repository.create(owner, "操作系统")

    assert await repository.get(CourseScope(principal=owner, course_id=course.id)) == course
    assert await repository.get(CourseScope(principal=stranger, course_id=course.id)) is None

    await database.dispose()


@pytest.mark.integration
async def test_course_creation_upserts_principal_under_concurrency(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    repository = CourseRepository(database)
    principal = Principal(subject="owner", authentication_method=AuthenticationMethod.LOCAL)

    courses = await asyncio.gather(
        repository.create(principal, "操作系统"),
        repository.create(principal, "数据库"),
    )

    assert len({course.user_id for course in courses}) == 1
    await database.dispose()


@pytest.mark.integration
async def test_database_rejects_cross_scope_document_object_link(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    repository = CourseRepository(database)
    owner = Principal(subject="owner", authentication_method=AuthenticationMethod.LOCAL)
    stranger = Principal(subject="stranger", authentication_method=AuthenticationMethod.LOCAL)
    owner_course = await repository.create(owner, "操作系统")
    stranger_course = await repository.create(stranger, "数据库")
    object_id = str(uuid4())

    async with database.session(owner) as session:
        session.add(
            StoredObjectModel(
                id=object_id,
                user_id=owner_course.user_id,
                course_id=owner_course.id,
                object_key="owner/course/original/object",
                purpose="original",
                sha256="a" * 64,
                size_bytes=10,
                media_type="application/pdf",
            )
        )

    with pytest.raises(IntegrityError):
        async with database.session(stranger) as session:
            session.add(
                DocumentModel(
                    id=str(uuid4()),
                    user_id=stranger_course.user_id,
                    course_id=stranger_course.id,
                    stored_object_id=object_id,
                    filename="chapter.pdf",
                    media_type="application/pdf",
                    corpus_role="corpus",
                    verified_sha256="a" * 64,
                    status="uploading",
                    deletion_epoch=0,
                )
            )
            await session.flush()

    await database.dispose()
