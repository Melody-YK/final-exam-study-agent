"""Scoped, versioned persistence for dense and lexical retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import VECTOR  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from study_agent.infrastructure.db.base import Base
from study_agent.infrastructure.db.models.core import new_id


class EmbeddingModelModel(Base):
    __tablename__ = "embedding_models"
    __table_args__ = (
        UniqueConstraint("id", "dimensions", name="uq_embedding_models_id_dimension"),
        UniqueConstraint(
            "provider_alias",
            "model_name",
            "dimensions",
            "distance_function",
            "contract_version",
            name="uq_embedding_models_identity",
        ),
        CheckConstraint("dimensions > 0", name="ck_embedding_models_dimensions"),
        CheckConstraint(
            "distance_function IN ('cosine', 'l2', 'inner_product')",
            name="ck_embedding_models_distance",
        ),
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_embedding_models_status",
        ),
        Index("ix_embedding_models_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_function: Mapped[str] = mapped_column(String(32), nullable=False, default="cosine")
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChunkEmbeddingModel(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_chunk_embeddings_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_chunk_embeddings_document_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_chunk_embeddings_revision_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            name="fk_chunk_embeddings_chunk_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_chunk_embeddings_model_dimension",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("chunk_id", "embedding_model_id", name="uq_chunk_embeddings_model"),
        CheckConstraint("dimensions > 0", name="ck_chunk_embeddings_dimensions"),
        CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name="ck_chunk_embeddings_vector_dimensions",
        ),
        Index(
            "ix_chunk_embeddings_scope_model",
            "user_id",
            "course_id",
            "embedding_model_id",
            "dimensions",
        ),
        Index("ix_chunk_embeddings_revision", "revision_id", "embedding_model_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IndexJobModel(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_index_jobs_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_index_jobs_document_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_index_jobs_revision_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_index_jobs_model_dimension",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "revision_id",
            "requested_provider",
            "requested_model",
            "contract_version",
            name="uq_index_jobs_revision_model",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_index_jobs_attempt_count"),
        CheckConstraint(
            "dimensions IS NULL OR dimensions > 0",
            name="ck_index_jobs_dimensions",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'index_blocked_provider', 'failed', 'succeeded')",
            name="ck_index_jobs_status",
        ),
        Index("ix_index_jobs_status_available", "status", "available_at", "created_at"),
        Index("ix_index_jobs_scope", "user_id", "course_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    requested_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(255), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    embedding_model_id: Mapped[str | None] = mapped_column(String(36))
    dimensions: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runner_id: Mapped[str | None] = mapped_column(String(128))
    failure_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LexicalManifestModel(Base):
    __tablename__ = "lexical_manifests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_lexical_manifests_course_user",
            ondelete="CASCADE",
        ),
        UniqueConstraint("course_id", "id", name="uq_lexical_manifests_course_id"),
        UniqueConstraint("course_id", "version_id", name="uq_lexical_manifests_version"),
        UniqueConstraint("storage_path", name="uq_lexical_manifests_storage_path"),
        CheckConstraint("chunk_count > 0", name="ck_lexical_manifests_chunk_count"),
        CheckConstraint(
            "status IN ('ready', 'active', 'superseded', 'failed')",
            name="ck_lexical_manifests_status",
        ),
        Index("ix_lexical_manifests_course_status", "course_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tokenizer_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dictionary_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    revision_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalTraceModel(Base):
    __tablename__ = "retrieval_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_retrieval_traces_course_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_retrieval_traces_model_dimension",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["course_id", "lexical_manifest_id"],
            ["lexical_manifests.course_id", "lexical_manifests.id"],
            name="fk_retrieval_traces_lexical_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("rrf_k > 0", name="ck_retrieval_traces_rrf_k"),
        CheckConstraint(
            "dimensions IS NULL OR dimensions > 0",
            name="ck_retrieval_traces_dimensions",
        ),
        Index("ix_retrieval_traces_scope_created", "user_id", "course_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    course_id: Mapped[str] = mapped_column(String(36), nullable=False)
    query_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    embedding_model_id: Mapped[str | None] = mapped_column(String(36))
    dimensions: Mapped[int | None] = mapped_column(Integer)
    lexical_manifest_id: Mapped[str | None] = mapped_column(String(36))
    rrf_k: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    dense_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    lexical_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    fused_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    rerank_candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    timings_ms: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    reranker_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reranker_fallback_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
