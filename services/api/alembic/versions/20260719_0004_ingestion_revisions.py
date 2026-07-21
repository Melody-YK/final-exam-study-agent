"""Persist validated parse attempts and immutable revision derivatives.

Revision ID: 20260719_0004
Revises: 20260719_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_revisions",
        sa.Column(
            "canonical_sha256",
            sa.String(length=64),
            server_default="0000000000000000000000000000000000000000000000000000000000000000",
            nullable=False,
        ),
    )
    op.add_column(
        "document_revisions",
        sa.Column("total_page_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "document_revisions",
        sa.Column(
            "chunker_version",
            sa.String(length=64),
            server_default="section-page-v1",
            nullable=False,
        ),
    )
    op.alter_column("document_revisions", "canonical_sha256", server_default=None)
    op.alter_column("document_revisions", "total_page_count", server_default=None)
    op.alter_column("document_revisions", "chunker_version", server_default=None)
    op.create_check_constraint(
        "ck_document_revisions_total_page_count",
        "document_revisions",
        "total_page_count >= 1",
    )
    op.create_index(
        "uq_document_revisions_parse_job",
        "document_revisions",
        ["parse_job_id"],
        unique=True,
        postgresql_where=sa.text("parse_job_id IS NOT NULL"),
    )

    op.create_table(
        "parse_attempt_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("parser_profile", sa.String(length=64), nullable=False),
        sa.Column("source_backend", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("total_page_count", sa.Integer(), nullable=False),
        sa.Column("requested_page_ordinals", postgresql.JSONB(), nullable=False),
        sa.Column("covered_page_ordinals", postgresql.JSONB(), nullable=False),
        sa.Column("canonical_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_parse_attempt_results_attempt"),
        sa.CheckConstraint(
            "total_page_count >= 1", name="ck_parse_attempt_results_total_page_count"
        ),
        sa.ForeignKeyConstraint(["artifact_id"], ["job_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["parse_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["job_id", "document_id", "course_id", "user_id"],
            [
                "parse_jobs.id",
                "parse_jobs.document_id",
                "parse_jobs.course_id",
                "parse_jobs.user_id",
            ],
            name="fk_parse_attempt_results_job_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id", name="uq_parse_attempt_results_artifact"),
        sa.UniqueConstraint("job_id", "attempt", name="uq_parse_attempt_results_job_attempt"),
    )
    op.create_index(
        "ix_parse_attempt_results_job_attempt",
        "parse_attempt_results",
        ["job_id", "attempt"],
    )

    op.create_table(
        "revision_pages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("page_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bbox_norm", postgresql.JSONB(), nullable=False),
        sa.Column("source_backend", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("raw_result_ref", sa.String(length=1024), nullable=False),
        sa.Column("quality", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("height >= 1", name="ck_revision_pages_height"),
        sa.CheckConstraint("page_ordinal >= 1", name="ck_revision_pages_ordinal"),
        sa.CheckConstraint("width >= 1", name="ck_revision_pages_width"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "page_ordinal", name="uq_revision_pages_ordinal"),
    )
    op.create_index("ix_revision_pages_revision", "revision_pages", ["revision_id", "page_ordinal"])

    op.create_table(
        "revision_blocks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("page_ordinal", sa.Integer(), nullable=False),
        sa.Column("block_id", sa.String(length=255), nullable=False),
        sa.Column("block_type", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bbox_norm", postgresql.JSONB(), nullable=False),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_backend", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("raw_result_ref", sa.String(length=1024), nullable=False),
        sa.Column("parent_block_id", sa.String(length=255), nullable=True),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_revision_blocks_confidence"
        ),
        sa.CheckConstraint("page_ordinal >= 1", name="ck_revision_blocks_page_ordinal"),
        sa.CheckConstraint("reading_order >= 0", name="ck_revision_blocks_reading_order"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_blocks_page",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "block_id", name="uq_revision_blocks_revision_block"),
        sa.UniqueConstraint(
            "revision_id",
            "page_ordinal",
            "reading_order",
            name="uq_revision_blocks_reading_order",
        ),
    )
    op.create_index(
        "ix_revision_blocks_revision_page",
        "revision_blocks",
        ["revision_id", "page_ordinal"],
    )

    op.create_table(
        "revision_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column("locator_kind", sa.String(length=16), nullable=False),
        sa.Column("page_ordinal", sa.Integer(), nullable=False),
        sa.Column("bbox_norm", postgresql.JSONB(), nullable=False),
        sa.Column("object_ref", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source_backend", sa.String(length=128), nullable=False),
        sa.Column("source_version", sa.String(length=64), nullable=False),
        sa.Column("raw_result_ref", sa.String(length=1024), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("page_ordinal >= 1", name="ck_revision_assets_page_ordinal"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_revision_assets_size"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_assets_page",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "asset_id", name="uq_revision_assets_revision_asset"),
    )
    op.create_index(
        "ix_revision_assets_revision_page",
        "revision_assets",
        ["revision_id", "page_ordinal"],
    )

    op.create_table(
        "revision_chunks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator_kind", sa.String(length=16), nullable=False),
        sa.Column("page_ordinal", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("source_block_ids", postgresql.JSONB(), nullable=False),
        sa.Column("token_count_estimate", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.CheckConstraint("ordinal >= 1", name="ck_revision_chunks_ordinal"),
        sa.CheckConstraint("page_ordinal >= 1", name="ck_revision_chunks_page_ordinal"),
        sa.CheckConstraint("token_count_estimate >= 1", name="ck_revision_chunks_token_count"),
        sa.ForeignKeyConstraint(["revision_id"], ["document_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["revision_id", "page_ordinal"],
            ["revision_pages.revision_id", "revision_pages.page_ordinal"],
            name="fk_revision_chunks_page",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("revision_id", "ordinal", name="uq_revision_chunks_ordinal"),
        sa.UniqueConstraint(
            "revision_id", "content_sha256", "ordinal", name="uq_revision_chunks_hash"
        ),
    )
    op.create_index(
        "ix_revision_chunks_revision_page",
        "revision_chunks",
        ["revision_id", "page_ordinal"],
    )


def downgrade() -> None:
    op.drop_index("ix_revision_chunks_revision_page", table_name="revision_chunks")
    op.drop_table("revision_chunks")
    op.drop_index("ix_revision_assets_revision_page", table_name="revision_assets")
    op.drop_table("revision_assets")
    op.drop_index("ix_revision_blocks_revision_page", table_name="revision_blocks")
    op.drop_table("revision_blocks")
    op.drop_index("ix_revision_pages_revision", table_name="revision_pages")
    op.drop_table("revision_pages")
    op.drop_index("ix_parse_attempt_results_job_attempt", table_name="parse_attempt_results")
    op.drop_table("parse_attempt_results")
    op.drop_index("uq_document_revisions_parse_job", table_name="document_revisions")
    op.drop_constraint(
        "ck_document_revisions_total_page_count", "document_revisions", type_="check"
    )
    op.drop_column("document_revisions", "chunker_version")
    op.drop_column("document_revisions", "total_page_count")
    op.drop_column("document_revisions", "canonical_sha256")
