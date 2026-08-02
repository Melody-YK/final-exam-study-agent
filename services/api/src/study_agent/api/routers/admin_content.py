"""Administrator-only, read-only learning-content endpoints."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request

from study_agent.api.errors import ApiProblem, ProblemCode
from study_agent.api.schemas.admin_content import (
    AdminCourseResponse,
    AdminCoursesResponse,
    AdminNoteResponse,
    AdminNotesResponse,
)
from study_agent.api.schemas.knowledge_graph import KnowledgeGraphResponse
from study_agent.config import Settings
from study_agent.identity.session import get_session_account
from study_agent.infrastructure.db.session import Database
from study_agent.modules.admin.content import (
    AdminContentError,
    AdminContentErrorCode,
    AdminContentService,
    AdminCourse,
    AdminNote,
)
from study_agent.modules.auth.service import AccountIdentity
from study_agent.modules.knowledge_graph import (
    MAX_GRAPH_EDGES,
    MAX_GRAPH_NODES,
    KnowledgeGraphForbidden,
    KnowledgeGraphNotFound,
    KnowledgeGraphService,
)
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer

router = APIRouter(prefix="/api/v1/admin", tags=["admin-content"])
SessionAccount = Annotated[AccountIdentity, Depends(get_session_account)]
NodeLimit = Annotated[int, Query(ge=3, le=MAX_GRAPH_NODES)]
EdgeLimit = Annotated[int, Query(ge=1, le=MAX_GRAPH_EDGES)]


@router.get("/courses", response_model=AdminCoursesResponse)
async def list_admin_courses(
    request: Request,
    identity: SessionAccount,
) -> AdminCoursesResponse:
    try:
        courses = await _content_service(request).list_courses(identity)
    except AdminContentError as exc:
        raise _content_problem(exc) from exc
    return AdminCoursesResponse(items=[_course_response(course) for course in courses])


@router.get("/courses/{course_id}/notes", response_model=AdminNotesResponse)
async def list_admin_course_notes(
    course_id: str,
    request: Request,
    identity: SessionAccount,
) -> AdminNotesResponse:
    try:
        notes = await _content_service(request).list_notes(identity, course_id)
    except AdminContentError as exc:
        raise _content_problem(exc) from exc
    return AdminNotesResponse(items=[_note_response(note) for note in notes])


@router.get(
    "/courses/{course_id}/knowledge-graph",
    response_model=KnowledgeGraphResponse,
)
async def get_admin_course_knowledge_graph(
    course_id: str,
    request: Request,
    identity: SessionAccount,
    node_limit: NodeLimit = 64,
    edge_limit: EdgeLimit = 160,
) -> KnowledgeGraphResponse:
    try:
        graph = await _knowledge_graph_service(request).get_admin_course_graph(
            identity,
            course_id,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )
    except KnowledgeGraphForbidden as exc:
        raise ApiProblem(
            status=403,
            code=ProblemCode.AUTH_FORBIDDEN,
            title="需要管理员权限",
        ) from exc
    except KnowledgeGraphNotFound as exc:
        raise ApiProblem(
            status=404,
            code=ProblemCode.RESOURCE_NOT_FOUND,
            title="课程不存在",
        ) from exc
    return KnowledgeGraphResponse.model_validate(graph)


def _content_service(request: Request) -> AdminContentService:
    return AdminContentService(cast(Database, request.app.state.database))


def _knowledge_graph_service(request: Request) -> KnowledgeGraphService:
    settings = cast(Settings, request.app.state.settings)
    return KnowledgeGraphService(
        cast(Database, request.app.state.database),
        ChineseTokenizer(settings.course_terms),
    )


def _course_response(course: AdminCourse) -> AdminCourseResponse:
    return AdminCourseResponse(
        id=course.id,
        title=course.title,
        lifecycle=course.lifecycle,
        owner_account_id=course.owner_account_id,
        owner_email=course.owner_email,
        owner_display_name=course.owner_display_name,
        owner_subject=course.owner_subject,
        document_count=course.document_count,
        note_count=course.note_count,
        created_at=course.created_at,
        updated_at=course.updated_at,
    )


def _note_response(note: AdminNote) -> AdminNoteResponse:
    return AdminNoteResponse(
        id=note.id,
        course_id=note.course_id,
        section_path=list(note.section_path),
        title=note.title,
        body_markdown=note.body_markdown,
        version=note.version,
        generation=note.generation,
        generated_by_model=note.generated_by_model,
        status=note.status,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _content_problem(exc: AdminContentError) -> ApiProblem:
    if exc.code is AdminContentErrorCode.FORBIDDEN:
        return ApiProblem(
            status=403,
            code=ProblemCode.AUTH_FORBIDDEN,
            title="需要管理员权限",
            detail=exc.detail,
        )
    return ApiProblem(
        status=404,
        code=ProblemCode.RESOURCE_NOT_FOUND,
        title="课程不存在",
        detail=exc.detail,
    )


__all__ = ["router"]
