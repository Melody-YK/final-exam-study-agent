from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from services.api.tests.integration.retrieval_helpers import seed_document_revision
from study_agent.config import AppMode, Settings
from study_agent.identity.principal import AuthenticationMethod, Principal
from study_agent.infrastructure.db.migrations import upgrade_database
from study_agent.infrastructure.db.models import DocumentModel
from study_agent.infrastructure.db.session import Database
from study_agent.main import create_app
from study_agent.modules.courses.repository import CourseRepository
from study_agent.modules.knowledge_graph import (
    KnowledgeGraphEdgeKind,
    KnowledgeGraphNodeKind,
    KnowledgeGraphService,
)
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer
from study_agent.providers.factory import ProviderRegistry
from study_agent.storage.local import LocalStorage


class StaticPrincipalProvider:
    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def resolve(self, client_host: str) -> Principal:
        del client_host
        return self._principal


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        database_url=SecretStr(database_url),
        local_storage_root=root,
        lexical_index_root=root / "lexical",
        course_terms=("进程", "调度"),
    )


def _provider_registry_without_upstream() -> ProviderRegistry:
    return ProviderRegistry(
        embedding_provider=None,
        chat_provider=None,
        http_client=None,
        owns_http_client=False,
    )


async def _seed_graph_sources(
    database: Database,
    principal: Principal,
) -> tuple[str, tuple[str, str]]:
    course = await CourseRepository(database).create(principal, "操作系统")
    async with database.session(principal) as session:
        first = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=(
                "进程 调度 进程 CPU",
                "进程 调度",
            ),
            active=True,
            preview=False,
        )
        second = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=("进程 同步 调度",),
            active=True,
            preview=False,
        )
        inactive = await seed_document_revision(
            session,
            user_id=course.user_id,
            course_id=course.id,
            text_chunks=("进程 调度 不应出现",),
            active=False,
            preview=False,
        )
        first_document = await session.get(DocumentModel, first.document_id)
        second_document = await session.get(DocumentModel, second.document_id)
        inactive_document = await session.get(DocumentModel, inactive.document_id)
        assert first_document is not None
        assert second_document is not None
        assert inactive_document is not None
        first_document.filename = "01-process.pdf"
        second_document.filename = "02-synchronization.pdf"
        inactive_document.filename = "00-inactive.pdf"
    return course.id, (first.document_id, second.document_id)


@pytest.mark.integration
async def test_knowledge_graph_is_traceable_bounded_and_deterministic(
    test_database_url: str,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    principal = Principal(
        subject="knowledge-graph-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, document_ids = await _seed_graph_sources(database, principal)
    service = KnowledgeGraphService(
        database,
        ChineseTokenizer(course_terms=("进程", "调度")),
    )
    try:
        graph = await service.get_course_graph(
            principal,
            course_id,
            node_limit=10,
            edge_limit=20,
        )
        repeated = await service.get_course_graph(
            principal,
            course_id,
            node_limit=10,
            edge_limit=20,
        )

        assert graph == repeated
        assert graph.active_document_count == 2
        assert graph.included_document_count == 2
        assert graph.source_chunk_count == 3
        assert graph.truncated is False
        assert graph.nodes[0].kind is KnowledgeGraphNodeKind.COURSE
        assert graph.nodes[0].label == "操作系统"

        document_nodes = [
            node for node in graph.nodes if node.kind is KnowledgeGraphNodeKind.DOCUMENT
        ]
        assert [node.label for node in document_nodes] == [
            "01-process.pdf",
            "02-synchronization.pdf",
        ]
        assert [node.document_id for node in document_nodes] == list(document_ids)

        concept_nodes = [
            node for node in graph.nodes if node.kind is KnowledgeGraphNodeKind.CONCEPT
        ]
        assert [(node.label, node.frequency) for node in concept_nodes] == [
            ("进程", 4),
            ("调度", 3),
        ]
        process = concept_nodes[0]
        assert process.document_count == 2
        assert process.occurrence_count == 3
        assert [occurrence.count for occurrence in process.occurrences] == [2, 1, 1]
        assert [occurrence.page_ordinal for occurrence in process.occurrences] == [1, 1, 1]
        assert process.occurrences[0].document_name == "01-process.pdf"
        assert process.occurrences[0].revision_id
        assert process.occurrences[0].chunk_id
        assert "进程" in process.occurrences[0].excerpt

        co_occurrence_edges = [
            edge for edge in graph.edges if edge.kind is KnowledgeGraphEdgeKind.CO_OCCURS
        ]
        assert len(co_occurrence_edges) == 1
        assert co_occurrence_edges[0].weight == 3
        assert {co_occurrence_edges[0].source, co_occurrence_edges[0].target} == {
            concept_nodes[0].id,
            concept_nodes[1].id,
        }

        bounded = await service.get_course_graph(
            principal,
            course_id,
            node_limit=4,
            edge_limit=4,
        )
        assert len(bounded.nodes) == 4
        assert len(bounded.edges) <= 4
        assert bounded.truncated is True

        async with database.session(principal) as session:
            document = await session.get(DocumentModel, document_ids[1])
            assert document is not None
            document.review_status = "pending"
        review_filtered = await service.get_course_graph(
            principal,
            course_id,
            node_limit=10,
            edge_limit=20,
        )
        assert review_filtered.active_document_count == 1
        assert [
            node.document_id
            for node in review_filtered.nodes
            if node.kind is KnowledgeGraphNodeKind.DOCUMENT
        ] == [document_ids[0]]
    finally:
        await database.dispose()


@pytest.mark.integration
async def test_knowledge_graph_route_hides_another_principals_course(
    test_database_url: str,
    tmp_path: Path,
) -> None:
    await upgrade_database(test_database_url)
    database = Database(test_database_url)
    owner = Principal(
        subject="knowledge-graph-route-owner",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    outsider = Principal(
        subject="knowledge-graph-route-outsider",
        authentication_method=AuthenticationMethod.LOCAL,
    )
    course_id, _ = await _seed_graph_sources(database, owner)
    settings = _settings(test_database_url, tmp_path)
    owner_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(owner),
        provider_registry=_provider_registry_without_upstream(),
    )
    outsider_app = create_app(
        settings=settings,
        database=database,
        storage=LocalStorage(tmp_path),
        principal_provider=StaticPrincipalProvider(outsider),
        provider_registry=_provider_registry_without_upstream(),
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=owner_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                f"/api/v1/courses/{course_id}/knowledge-graph",
                params={"node_limit": 10, "edge_limit": 20},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["course_id"] == course_id
        assert body["nodes"][0]["kind"] == "course"
        assert {node["label"] for node in body["nodes"] if node["kind"] == "concept"} == {
            "进程",
            "调度",
        }

        async with AsyncClient(
            transport=ASGITransport(app=outsider_app),
            base_url="http://testserver",
        ) as client:
            hidden = await client.get(f"/api/v1/courses/{course_id}/knowledge-graph")
        assert hidden.status_code == 404
        assert hidden.json()["code"] == "RESOURCE_NOT_FOUND"
    finally:
        await database.dispose()
