from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import ChunkEmbeddingModel, EmbeddingModelModel
from study_agent.infrastructure.db.session import Database
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.ingestion.index_runner import EmbeddingModelIdentity
from study_agent.modules.retrieval.dense import DenseRetriever


@pytest.mark.integration
async def test_exact_dense_retrieval_filters_principal_course_document_model_and_dimension(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(subject="retrieval-owner", authentication_method=AuthenticationMethod.LOCAL)
    outsider = Principal(
        subject="retrieval-outsider", authentication_method=AuthenticationMethod.LOCAL
    )
    owner_course = await CourseRepository(database).create(owner, "操作系统")
    outsider_course = await CourseRepository(database).create(outsider, "其他课程")
    model_2 = EmbeddingModelModel(
        id=str(uuid4()),
        provider_alias="test",
        model_name="tiny",
        dimensions=2,
        distance_function="cosine",
        contract_version="1",
        status="active",
    )
    model_3 = EmbeddingModelModel(
        id=str(uuid4()),
        provider_alias="test",
        model_name="tiny",
        dimensions=3,
        distance_function="cosine",
        contract_version="1",
        status="active",
    )
    async with database.session(owner) as session:
        first = await seed_document_revision(
            session,
            user_id=owner_course.user_id,
            course_id=owner_course.id,
            text_chunks=["进程调度", "文件系统", "设备管理"],
            active=True,
            preview=False,
        )
        second = await seed_document_revision(
            session,
            user_id=owner_course.user_id,
            course_id=owner_course.id,
            text_chunks=["内存管理"],
            active=True,
            preview=False,
        )
        session.add_all([model_2, model_3])
        await session.flush()
        vectors = ([1.0, 0.0], [0.0, 1.0], [0.6, 0.8])
        scoped_chunks = (
            (first, first.chunk_ids[0], vectors[0]),
            (first, first.chunk_ids[1], vectors[1]),
            (second, second.chunk_ids[0], vectors[2]),
        )
        for seeded, chunk_id, vector in scoped_chunks:
            session.add(
                ChunkEmbeddingModel(
                    id=str(uuid4()),
                    user_id=owner_course.user_id,
                    course_id=owner_course.id,
                    document_id=seeded.document_id,
                    revision_id=seeded.revision_id,
                    chunk_id=chunk_id,
                    embedding_model_id=model_2.id,
                    dimensions=2,
                    embedding=list(vector),
                )
            )
        session.add(
            ChunkEmbeddingModel(
                id=str(uuid4()),
                user_id=owner_course.user_id,
                course_id=owner_course.id,
                document_id=first.document_id,
                revision_id=first.revision_id,
                chunk_id=first.chunk_ids[0],
                embedding_model_id=model_3.id,
                dimensions=3,
                embedding=[0.0, 1.0, 0.0],
            )
        )
    async with database.session(outsider) as session:
        outsider_revision = await seed_document_revision(
            session,
            user_id=outsider_course.user_id,
            course_id=outsider_course.id,
            text_chunks=["不应泄露"],
            active=True,
            preview=False,
        )
        session.add(
            ChunkEmbeddingModel(
                id=str(uuid4()),
                user_id=outsider_course.user_id,
                course_id=outsider_course.id,
                document_id=outsider_revision.document_id,
                revision_id=outsider_revision.revision_id,
                chunk_id=outsider_revision.chunk_ids[0],
                embedding_model_id=model_2.id,
                dimensions=2,
                embedding=[1.0, 0.0],
            )
        )

    async with database.session(owner) as session:
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    ChunkEmbeddingModel(
                        id=str(uuid4()),
                        user_id=owner_course.user_id,
                        course_id=owner_course.id,
                        document_id=first.document_id,
                        revision_id=first.revision_id,
                        chunk_id=first.chunk_ids[1],
                        embedding_model_id=model_2.id,
                        dimensions=3,
                        embedding=[0.0, 1.0, 0.0],
                    )
                )
                await session.flush()

        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                session.add(
                    ChunkEmbeddingModel(
                        id=str(uuid4()),
                        user_id=owner_course.user_id,
                        course_id=owner_course.id,
                        document_id=first.document_id,
                        revision_id=first.revision_id,
                        chunk_id=first.chunk_ids[2],
                        embedding_model_id=model_2.id,
                        dimensions=2,
                        embedding=[0.0, 1.0, 0.0],
                    )
                )
                await session.flush()

    retriever = DenseRetriever(database)
    identity = EmbeddingModelIdentity(
        id=model_2.id,
        provider="test",
        model="tiny",
        dimensions=2,
        distance="cosine",
        contract_version="1",
    )
    results = await retriever.retrieve(
        owner,
        owner_course.id,
        [1.0, 0.0],
        model=identity,
        limit=10,
    )
    filtered = await retriever.retrieve(
        owner,
        owner_course.id,
        [1.0, 0.0],
        model=identity,
        document_ids=frozenset({second.document_id}),
        limit=10,
    )
    cross_scope = await retriever.retrieve(
        outsider,
        owner_course.id,
        [1.0, 0.0],
        model=identity,
        limit=10,
    )
    await database.dispose()

    assert [item.chunk_id for item in results] == [
        first.chunk_ids[0],
        second.chunk_ids[0],
        first.chunk_ids[1],
    ]
    assert [item.document_id for item in filtered] == [second.document_id]
    assert cross_scope == ()

    with pytest.raises(ValueError, match="dimension"):
        await retriever.retrieve(
            owner,
            owner_course.id,
            [1.0, 0.0, 0.0],
            model=identity,
            limit=10,
        )
