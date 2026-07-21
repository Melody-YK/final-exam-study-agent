"""Add scoped dense and lexical retrieval persistence.

Revision ID: 20260719_0005
Revises: 20260719_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0005"
down_revision: str | None = "20260719_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_revision_chunks_id_revision",
        "revision_chunks",
        ["id", "revision_id"],
    )
    op.create_table(
        "embedding_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_alias", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_function", sa.String(length=32), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("dimensions > 0", name="ck_embedding_models_dimensions"),
        sa.CheckConstraint(
            "distance_function IN ('cosine', 'l2', 'inner_product')",
            name="ck_embedding_models_distance",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_embedding_models_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "dimensions", name="uq_embedding_models_id_dimension"),
        sa.UniqueConstraint(
            "provider_alias",
            "model_name",
            "dimensions",
            "distance_function",
            "contract_version",
            name="uq_embedding_models_identity",
        ),
    )
    op.create_index("ix_embedding_models_status", "embedding_models", ["status"])

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=36), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("dimensions > 0", name="ck_chunk_embeddings_dimensions"),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name="ck_chunk_embeddings_vector_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_chunk_embeddings_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_chunk_embeddings_document_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_chunk_embeddings_revision_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "revision_id"],
            ["revision_chunks.id", "revision_chunks.revision_id"],
            name="fk_chunk_embeddings_chunk_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_chunk_embeddings_model_dimension",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "embedding_model_id", name="uq_chunk_embeddings_model"),
    )
    op.create_index(
        "ix_chunk_embeddings_scope_model",
        "chunk_embeddings",
        ["user_id", "course_id", "embedding_model_id", "dimensions"],
    )
    op.create_index(
        "ix_chunk_embeddings_revision",
        "chunk_embeddings",
        ["revision_id", "embedding_model_id"],
    )

    op.create_table(
        "index_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("requested_provider", sa.String(length=64), nullable=False),
        sa.Column("requested_model", sa.String(length=255), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=36), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runner_id", sa.String(length=128), nullable=True),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_index_jobs_attempt_count"),
        sa.CheckConstraint(
            "dimensions IS NULL OR dimensions > 0",
            name="ck_index_jobs_dimensions",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'index_blocked_provider', 'failed', 'succeeded')",
            name="ck_index_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_index_jobs_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "course_id", "user_id"],
            ["documents.id", "documents.course_id", "documents.user_id"],
            name="fk_index_jobs_document_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "revision_id"],
            ["document_revisions.document_id", "document_revisions.id"],
            name="fk_index_jobs_revision_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_index_jobs_model_dimension",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "revision_id",
            "requested_provider",
            "requested_model",
            "contract_version",
            name="uq_index_jobs_revision_model",
        ),
    )
    op.create_index(
        "ix_index_jobs_status_available",
        "index_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index("ix_index_jobs_scope", "index_jobs", ["user_id", "course_id", "document_id"])

    op.create_table(
        "lexical_manifests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("document_set_hash", sa.String(length=64), nullable=False),
        sa.Column("tokenizer_version", sa.String(length=128), nullable=False),
        sa.Column("dictionary_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("revision_ids", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("chunk_count > 0", name="ck_lexical_manifests_chunk_count"),
        sa.CheckConstraint(
            "status IN ('ready', 'active', 'superseded', 'failed')",
            name="ck_lexical_manifests_status",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_lexical_manifests_course_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "id", name="uq_lexical_manifests_course_id"),
        sa.UniqueConstraint("course_id", "version_id", name="uq_lexical_manifests_version"),
        sa.UniqueConstraint("storage_path", name="uq_lexical_manifests_storage_path"),
    )
    op.create_index(
        "ix_lexical_manifests_course_status",
        "lexical_manifests",
        ["course_id", "status", "created_at"],
    )
    op.create_foreign_key(
        "fk_courses_active_lexical_manifest",
        "courses",
        "lexical_manifests",
        ["id", "active_lexical_index_id"],
        ["course_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "retrieval_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("scope_document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_model_id", sa.String(length=36), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("lexical_manifest_id", sa.String(length=36), nullable=True),
        sa.Column("rrf_k", sa.Integer(), nullable=False),
        sa.Column("dense_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("lexical_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("fused_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("rerank_candidates", postgresql.JSONB(), nullable=False),
        sa.Column("timings_ms", postgresql.JSONB(), nullable=False),
        sa.Column("reranker_applied", sa.Boolean(), nullable=False),
        sa.Column("reranker_fallback_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("rrf_k > 0", name="ck_retrieval_traces_rrf_k"),
        sa.CheckConstraint(
            "dimensions IS NULL OR dimensions > 0",
            name="ck_retrieval_traces_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "user_id"],
            ["courses.id", "courses.user_id"],
            name="fk_retrieval_traces_course_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_model_id", "dimensions"],
            ["embedding_models.id", "embedding_models.dimensions"],
            name="fk_retrieval_traces_model_dimension",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["course_id", "lexical_manifest_id"],
            ["lexical_manifests.course_id", "lexical_manifests.id"],
            name="fk_retrieval_traces_lexical_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retrieval_traces_scope_created",
        "retrieval_traces",
        ["user_id", "course_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_traces_scope_created", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
    op.drop_constraint("fk_courses_active_lexical_manifest", "courses", type_="foreignkey")
    op.drop_index("ix_lexical_manifests_course_status", table_name="lexical_manifests")
    op.drop_table("lexical_manifests")
    op.drop_index("ix_index_jobs_scope", table_name="index_jobs")
    op.drop_index("ix_index_jobs_status_available", table_name="index_jobs")
    op.drop_table("index_jobs")
    op.drop_index("ix_chunk_embeddings_revision", table_name="chunk_embeddings")
    op.drop_index("ix_chunk_embeddings_scope_model", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("ix_embedding_models_status", table_name="embedding_models")
    op.drop_table("embedding_models")
    op.drop_constraint(
        "uq_revision_chunks_id_revision",
        "revision_chunks",
        type_="unique",
    )
