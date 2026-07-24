"""HTTP response models for the read-only course knowledge graph."""

from pydantic import BaseModel, ConfigDict, Field

from study_agent.modules.knowledge_graph import (
    KnowledgeGraphEdgeKind,
    KnowledgeGraphNodeKind,
)


class KnowledgeGraphOccurrenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    document_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    page_ordinal: int = Field(ge=1)
    chunk_ordinal: int = Field(ge=1)
    count: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=182)


class KnowledgeGraphNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    kind: KnowledgeGraphNodeKind
    label: str = Field(min_length=1, max_length=1024)
    document_id: str | None = None
    revision_id: str | None = None
    page_count: int | None = Field(default=None, ge=1)
    frequency: int | None = Field(default=None, ge=1)
    document_count: int | None = Field(default=None, ge=1)
    occurrence_count: int | None = Field(default=None, ge=1)
    occurrences: list[KnowledgeGraphOccurrenceResponse] = Field(default_factory=list)
    occurrences_truncated: bool = False


class KnowledgeGraphEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    kind: KnowledgeGraphEdgeKind
    weight: int = Field(ge=1)


class KnowledgeGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    course_id: str = Field(min_length=1)
    tokenizer_version: str = Field(min_length=1)
    active_document_count: int = Field(ge=0)
    included_document_count: int = Field(ge=0)
    source_chunk_count: int = Field(ge=0)
    node_limit: int = Field(ge=3)
    edge_limit: int = Field(ge=1)
    truncated: bool
    nodes: list[KnowledgeGraphNodeResponse]
    edges: list[KnowledgeGraphEdgeResponse]


__all__ = [
    "KnowledgeGraphEdgeResponse",
    "KnowledgeGraphNodeResponse",
    "KnowledgeGraphOccurrenceResponse",
    "KnowledgeGraphResponse",
]
