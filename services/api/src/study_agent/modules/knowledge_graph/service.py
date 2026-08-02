"""Deterministic, read-only knowledge graph derived from active source chunks."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.identity.principal import Principal
from study_agent.infrastructure.db.models import (
    AccountModel,
    CourseModel,
    DocumentModel,
    DocumentRevisionModel,
    RevisionChunkModel,
    UserModel,
)
from study_agent.infrastructure.db.session import Database
from study_agent.modules.auth.service import AccountIdentity, AccountRole, AccountStatus
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer

MAX_GRAPH_NODES = 100
MAX_GRAPH_EDGES = 300
MAX_DOCUMENT_NODES = 24
MAX_SOURCE_CHUNKS = 4_000
MAX_OCCURRENCES_PER_CONCEPT = 12
MIN_CONCEPT_FREQUENCY = 2
_ADMIN_KNOWLEDGE_GRAPH_ACTOR = "admin-knowledge-graph"

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
        "count",
        "do",
        "false",
        "int",
        "item",
        "out",
        "return",
        "true",
        "value",
        "void",
        "while",
        "一个",
        "一种",
        "以及",
        "但是",
        "可以",
        "因为",
        "如果",
        "对于",
        "就是",
        "并且",
        "我们",
        "所以",
        "或者",
        "通过",
        "这个",
        "这些",
        "进行",
        "其中",
        "具有",
        "两个",
        "使用",
        "利用",
        "实现",
        "指定",
        "描述",
        "操作",
        "管理",
        "表示",
        "解决",
        "进入",
        "执行",
        "问题",
        "关系",
        "机制",
        "的是",
        "在",
        "是",
        "和",
        "与",
        "或",
        "及",
        "的",
        "了",
        "为",
        "而",
        "中",
    }
)


class KnowledgeGraphNodeKind(StrEnum):
    COURSE = "course"
    DOCUMENT = "document"
    CONCEPT = "concept"


class KnowledgeGraphEdgeKind(StrEnum):
    CONTAINS = "contains"
    MENTIONS = "mentions"
    CO_OCCURS = "co_occurs"


class KnowledgeGraphNotFound(LookupError):
    """The principal cannot access the requested active course."""


class KnowledgeGraphForbidden(PermissionError):
    """The account is not allowed to inspect cross-user course graphs."""


@dataclass(frozen=True, slots=True)
class KnowledgeGraphOccurrence:
    document_id: str
    document_name: str
    revision_id: str
    chunk_id: str
    locator_kind: str
    page_ordinal: int
    section_path: tuple[str, ...]
    chunk_ordinal: int
    count: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class KnowledgeGraphNode:
    id: str
    kind: KnowledgeGraphNodeKind
    label: str
    document_id: str | None = None
    revision_id: str | None = None
    page_count: int | None = None
    frequency: int | None = None
    document_count: int | None = None
    occurrence_count: int | None = None
    occurrences: tuple[KnowledgeGraphOccurrence, ...] = ()
    occurrences_truncated: bool = False


@dataclass(frozen=True, slots=True)
class KnowledgeGraphEdge:
    id: str
    source: str
    target: str
    kind: KnowledgeGraphEdgeKind
    weight: int


@dataclass(frozen=True, slots=True)
class KnowledgeGraph:
    course_id: str
    tokenizer_version: str
    active_document_count: int
    included_document_count: int
    source_chunk_count: int
    node_limit: int
    edge_limit: int
    truncated: bool
    nodes: tuple[KnowledgeGraphNode, ...]
    edges: tuple[KnowledgeGraphEdge, ...]


@dataclass(frozen=True, slots=True)
class _DocumentSource:
    id: str
    filename: str
    revision_id: str
    page_count: int


class KnowledgeGraphService:
    """Project active course sources into a bounded knowledge graph."""

    def __init__(
        self,
        database: Database,
        tokenizer: ChineseTokenizer,
    ) -> None:
        self._database = database
        self._tokenizer = tokenizer

    async def get_course_graph(
        self,
        principal: Principal,
        course_id: str,
        *,
        node_limit: int = 64,
        edge_limit: int = 160,
    ) -> KnowledgeGraph:
        _validate_limits(node_limit, edge_limit)

        async with self._database.session(principal) as session:
            course = await session.scalar(
                select(CourseModel)
                .join(UserModel, UserModel.id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.lifecycle == "active",
                    CourseModel.deleted_at.is_(None),
                    UserModel.subject == principal.subject,
                    UserModel.authentication_method == principal.authentication_method.value,
                )
            )
            if course is None:
                raise KnowledgeGraphNotFound
            return await self._project_course(
                session,
                course,
                node_limit=node_limit,
                edge_limit=edge_limit,
            )

    async def get_admin_course_graph(
        self,
        actor: AccountIdentity,
        course_id: str,
        *,
        node_limit: int = 64,
        edge_limit: int = 160,
    ) -> KnowledgeGraph:
        _validate_limits(node_limit, edge_limit)
        if (
            actor.account.role is not AccountRole.ADMIN
            or actor.account.status is not AccountStatus.ACTIVE
        ):
            raise KnowledgeGraphForbidden

        async with self._database.system_session(_ADMIN_KNOWLEDGE_GRAPH_ACTOR) as session:
            course = await session.scalar(
                select(CourseModel)
                .join(AccountModel, AccountModel.user_id == CourseModel.user_id)
                .where(
                    CourseModel.id == course_id,
                    CourseModel.lifecycle == "active",
                    CourseModel.deleted_at.is_(None),
                    AccountModel.role == AccountRole.USER.value,
                )
            )
            if course is None:
                raise KnowledgeGraphNotFound
            return await self._project_course(
                session,
                course,
                node_limit=node_limit,
                edge_limit=edge_limit,
            )

    async def _project_course(
        self,
        session: AsyncSession,
        course: CourseModel,
        *,
        node_limit: int,
        edge_limit: int,
    ) -> KnowledgeGraph:
        document_filter = (
            DocumentModel.user_id == course.user_id,
            DocumentModel.course_id == course.id,
            DocumentModel.deleted_at.is_(None),
            DocumentModel.status == "ready",
            DocumentModel.corpus_role == "corpus",
            DocumentModel.review_status == "approved",
            DocumentModel.active_revision_id.is_not(None),
        )
        active_document_count = int(
            await session.scalar(
                select(func.count()).select_from(DocumentModel).where(*document_filter)
            )
            or 0
        )
        document_limit = min(
            MAX_DOCUMENT_NODES,
            node_limit - 2,
            edge_limit,
        )
        document_rows = list(
            (
                await session.execute(
                    select(DocumentModel, DocumentRevisionModel)
                    .join(
                        DocumentRevisionModel,
                        (DocumentRevisionModel.id == DocumentModel.active_revision_id)
                        & (DocumentRevisionModel.document_id == DocumentModel.id),
                    )
                    .where(*document_filter)
                    .order_by(
                        func.lower(DocumentModel.filename),
                        DocumentModel.filename,
                        DocumentModel.id,
                    )
                    .limit(document_limit)
                )
            ).tuples()
        )
        documents = tuple(
            _DocumentSource(
                id=document.id,
                filename=document.filename,
                revision_id=revision.id,
                page_count=revision.total_page_count,
            )
            for document, revision in document_rows
        )
        revision_ids = [document.revision_id for document in documents]
        chunks: list[RevisionChunkModel] = []
        source_chunks_truncated = False
        if revision_ids:
            chunk_rows = list(
                await session.scalars(
                    select(RevisionChunkModel)
                    .where(RevisionChunkModel.revision_id.in_(revision_ids))
                    .order_by(
                        RevisionChunkModel.revision_id,
                        RevisionChunkModel.page_ordinal,
                        RevisionChunkModel.ordinal,
                        RevisionChunkModel.id,
                    )
                    .limit(MAX_SOURCE_CHUNKS + 1)
                )
            )
            source_chunks_truncated = len(chunk_rows) > MAX_SOURCE_CHUNKS
            chunks = chunk_rows[:MAX_SOURCE_CHUNKS]

        return self._build_graph(
            course_id=course.id,
            course_title=course.title,
            active_document_count=active_document_count,
            documents=documents,
            chunks=chunks,
            source_chunks_truncated=source_chunks_truncated,
            node_limit=node_limit,
            edge_limit=edge_limit,
        )

    def _build_graph(
        self,
        *,
        course_id: str,
        course_title: str,
        active_document_count: int,
        documents: tuple[_DocumentSource, ...],
        chunks: list[RevisionChunkModel],
        source_chunks_truncated: bool,
        node_limit: int,
        edge_limit: int,
    ) -> KnowledgeGraph:
        document_by_revision = {document.revision_id: document for document in documents}
        document_rank = {document.id: rank for rank, document in enumerate(documents)}
        frequency: Counter[str] = Counter()
        document_frequency: dict[str, set[str]] = defaultdict(set)
        document_concept_counts: Counter[tuple[str, str]] = Counter()
        occurrences: dict[str, list[KnowledgeGraphOccurrence]] = defaultdict(list)
        chunk_concepts: dict[str, set[str]] = {}

        for chunk in chunks:
            document = document_by_revision.get(chunk.revision_id)
            if document is None:
                continue
            concept_counts = Counter(
                token
                for token in self._tokenizer.tokenize(chunk.text)
                if _is_concept(token, self._tokenizer.course_terms)
            )
            chunk_concepts[chunk.id] = set(concept_counts)
            for concept, count in sorted(concept_counts.items()):
                frequency[concept] += count
                document_frequency[concept].add(document.id)
                document_concept_counts[(document.id, concept)] += count
                occurrences[concept].append(
                    KnowledgeGraphOccurrence(
                        document_id=document.id,
                        document_name=document.filename,
                        revision_id=document.revision_id,
                        chunk_id=chunk.id,
                        locator_kind=chunk.locator_kind,
                        page_ordinal=chunk.page_ordinal,
                        section_path=tuple(chunk.section_path),
                        chunk_ordinal=chunk.ordinal,
                        count=count,
                        excerpt=_excerpt(chunk.text, concept),
                    )
                )

        ranked_concepts = sorted(
            (concept for concept, count in frequency.items() if count >= MIN_CONCEPT_FREQUENCY),
            key=lambda concept: (-frequency[concept], concept),
        )
        available_edge_slots = max(0, edge_limit - len(documents))
        concept_limit = min(
            node_limit - 1 - len(documents),
            available_edge_slots,
        )
        selected_concepts = tuple(ranked_concepts[:concept_limit])
        concept_rank = {concept: rank for rank, concept in enumerate(selected_concepts)}
        concept_ids = {
            concept: f"concept:{hashlib.sha256(concept.encode('utf-8')).hexdigest()}"
            for concept in selected_concepts
        }

        nodes: list[KnowledgeGraphNode] = [
            KnowledgeGraphNode(
                id=f"course:{course_id}",
                kind=KnowledgeGraphNodeKind.COURSE,
                label=course_title,
            )
        ]
        nodes.extend(
            KnowledgeGraphNode(
                id=f"document:{document.id}",
                kind=KnowledgeGraphNodeKind.DOCUMENT,
                label=document.filename,
                document_id=document.id,
                revision_id=document.revision_id,
                page_count=document.page_count,
            )
            for document in documents
        )
        occurrence_truncated = False
        for concept in selected_concepts:
            concept_occurrences = occurrences[concept]
            concept_occurrences.sort(
                key=lambda item: (
                    document_rank[item.document_id],
                    item.page_ordinal,
                    item.chunk_ordinal,
                    item.chunk_id,
                )
            )
            is_truncated = len(concept_occurrences) > MAX_OCCURRENCES_PER_CONCEPT
            occurrence_truncated = occurrence_truncated or is_truncated
            nodes.append(
                KnowledgeGraphNode(
                    id=concept_ids[concept],
                    kind=KnowledgeGraphNodeKind.CONCEPT,
                    label=concept,
                    frequency=frequency[concept],
                    document_count=len(document_frequency[concept]),
                    occurrence_count=len(concept_occurrences),
                    occurrences=tuple(concept_occurrences[:MAX_OCCURRENCES_PER_CONCEPT]),
                    occurrences_truncated=is_truncated,
                )
            )

        course_node_id = f"course:{course_id}"
        contains_edges = [
            KnowledgeGraphEdge(
                id=f"edge:contains:{document.id}",
                source=course_node_id,
                target=f"document:{document.id}",
                kind=KnowledgeGraphEdgeKind.CONTAINS,
                weight=1,
            )
            for document in documents
        ]

        primary_mentions: list[KnowledgeGraphEdge] = []
        secondary_mentions: list[KnowledgeGraphEdge] = []
        for concept in selected_concepts:
            ranked_documents = sorted(
                (
                    (document, document_concept_counts[(document.id, concept)])
                    for document in documents
                    if document_concept_counts[(document.id, concept)] > 0
                ),
                key=lambda item: (-item[1], document_rank[item[0].id]),
            )
            for index, (document, count) in enumerate(ranked_documents):
                edge = KnowledgeGraphEdge(
                    id=f"edge:mentions:{document.id}:{concept_ids[concept].removeprefix('concept:')}",
                    source=f"document:{document.id}",
                    target=concept_ids[concept],
                    kind=KnowledgeGraphEdgeKind.MENTIONS,
                    weight=count,
                )
                if index == 0:
                    primary_mentions.append(edge)
                else:
                    secondary_mentions.append(edge)
        primary_mentions.sort(
            key=lambda edge: concept_rank[_concept_for_node_id(edge.target, concept_ids)]
        )
        secondary_mentions.sort(
            key=lambda edge: (
                document_rank[edge.source.removeprefix("document:")],
                concept_rank[_concept_for_node_id(edge.target, concept_ids)],
            )
        )

        co_occurrence_counts: Counter[tuple[str, str]] = Counter()
        selected_set = set(selected_concepts)
        for chunk_id in sorted(chunk_concepts):
            present = sorted(
                chunk_concepts[chunk_id] & selected_set,
                key=concept_rank.__getitem__,
            )
            for left, right in combinations(present, 2):
                co_occurrence_counts[(left, right)] += 1
        co_occurrence_edges = [
            KnowledgeGraphEdge(
                id=f"edge:co-occurs:{concept_ids[left].removeprefix('concept:')}:{concept_ids[right].removeprefix('concept:')}",
                source=concept_ids[left],
                target=concept_ids[right],
                kind=KnowledgeGraphEdgeKind.CO_OCCURS,
                weight=weight,
            )
            for (left, right), weight in sorted(
                co_occurrence_counts.items(),
                key=lambda item: (
                    -item[1],
                    concept_rank[item[0][0]],
                    concept_rank[item[0][1]],
                ),
            )
        ]
        candidate_edges = (
            contains_edges + primary_mentions + secondary_mentions + co_occurrence_edges
        )
        edges = tuple(candidate_edges[:edge_limit])
        truncated = any(
            (
                active_document_count > len(documents),
                source_chunks_truncated,
                len(ranked_concepts) > len(selected_concepts),
                len(candidate_edges) > len(edges),
                occurrence_truncated,
            )
        )
        return KnowledgeGraph(
            course_id=course_id,
            tokenizer_version=self._tokenizer.version,
            active_document_count=active_document_count,
            included_document_count=len(documents),
            source_chunk_count=len(chunks),
            node_limit=node_limit,
            edge_limit=edge_limit,
            truncated=truncated,
            nodes=tuple(nodes),
            edges=edges,
        )


def _is_concept(token: str, course_terms: tuple[str, ...]) -> bool:
    if token in _STOP_WORDS or not token or len(token) > 64 or token.isnumeric():
        return False
    if token in course_terms:
        return True
    if token.isascii() and token.isalnum() and len(token) < 3:
        return False
    if len(token) < 2:
        return False
    return any(character.isalnum() for character in token)


def _excerpt(text: str, concept: str, limit: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    index = normalized.lower().find(concept)
    if index < 0:
        return f"{normalized[: limit - 1]}…"
    start = max(0, index - limit // 3)
    end = min(len(normalized), start + limit)
    start = max(0, end - limit)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(normalized) else ""
    return f"{prefix}{normalized[start:end]}{suffix}"


def _concept_for_node_id(
    node_id: str,
    concept_ids: dict[str, str],
) -> str:
    return next(concept for concept, candidate in concept_ids.items() if candidate == node_id)


def _validate_limits(node_limit: int, edge_limit: int) -> None:
    if not 3 <= node_limit <= MAX_GRAPH_NODES:
        raise ValueError(f"node_limit must be between 3 and {MAX_GRAPH_NODES}")
    if not 1 <= edge_limit <= MAX_GRAPH_EDGES:
        raise ValueError(f"edge_limit must be between 1 and {MAX_GRAPH_EDGES}")


__all__ = [
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_NODES",
    "KnowledgeGraph",
    "KnowledgeGraphEdge",
    "KnowledgeGraphEdgeKind",
    "KnowledgeGraphForbidden",
    "KnowledgeGraphNode",
    "KnowledgeGraphNodeKind",
    "KnowledgeGraphNotFound",
    "KnowledgeGraphOccurrence",
    "KnowledgeGraphService",
]
