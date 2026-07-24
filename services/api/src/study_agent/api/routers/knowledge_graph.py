"""Read-only API for a deterministic course knowledge graph projection."""

from typing import Annotated, cast

from fastapi import APIRouter, Query, Request

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.api.schemas.knowledge_graph import KnowledgeGraphResponse
from study_agent.config import Settings
from study_agent.identity.principal import Principal
from study_agent.identity.session import get_request_principal
from study_agent.infrastructure.db.session import Database
from study_agent.modules.knowledge_graph import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    KnowledgeGraphNotFound,
    KnowledgeGraphService,
)
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer

router = APIRouter(prefix="/api/v1", tags=["knowledge-graph"])
NodeLimit = Annotated[int, Query(ge=3, le=MAX_GRAPH_NODES)]
EdgeLimit = Annotated[int, Query(ge=1, le=MAX_GRAPH_EDGES)]


async def _principal(request: Request) -> Principal:
    return await get_request_principal(request)


def _service(request: Request) -> KnowledgeGraphService:
    settings = cast(Settings, request.app.state.settings)
    return KnowledgeGraphService(
        cast(Database, request.app.state.database),
        ChineseTokenizer(settings.course_terms),
    )


@router.get(
    "/courses/{course_id}/knowledge-graph",
    response_model=KnowledgeGraphResponse,
)
async def get_course_knowledge_graph(
    course_id: str,
    request: Request,
    node_limit: NodeLimit = 64,
    edge_limit: EdgeLimit = 160,
) -> KnowledgeGraphResponse:
    try:
        graph = await _service(request).get_course_graph(
            await _principal(request),
            course_id,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )
    except KnowledgeGraphNotFound as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return KnowledgeGraphResponse.model_validate(graph)


__all__ = ["router"]
