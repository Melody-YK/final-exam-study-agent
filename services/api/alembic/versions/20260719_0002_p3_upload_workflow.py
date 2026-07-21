"""Add the scoped two-stage upload and idempotency invariants.

Revision ID: 20260719_0002
Revises: 20260718_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0002"
down_revision: str | None = "20260718_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("user_id", sa.String(length=36), nullable=True))
    op.execute(
        "UPDATE documents SET user_id = courses.user_id "
        "FROM courses WHERE documents.course_id = courses.id"
    )
    op.alter_column("documents", "user_id", nullable=False)
    op.create_foreign_key(
        "fk_documents_user", "documents", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )

    op.add_column("upload_sessions", sa.Column("document_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_upload_sessions_document",
        "upload_sessions",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "idempotency_records",
        sa.Column(
            "actor_authentication_method",
            sa.String(length=32),
            server_default="local",
            nullable=False,
        ),
    )
    op.alter_column("idempotency_records", "actor_authentication_method", server_default=None)
    op.drop_constraint("uq_idempotency_actor_operation_key", "idempotency_records", type_="unique")
    op.create_unique_constraint(
        "uq_idempotency_actor_operation_key",
        "idempotency_records",
        [
            "actor_subject",
            "actor_authentication_method",
            "operation",
            "idempotency_key",
        ],
    )

    op.create_unique_constraint("uq_courses_id_user", "courses", ["id", "user_id"])
    op.create_unique_constraint(
        "uq_stored_objects_id_course_user",
        "stored_objects",
        ["id", "course_id", "user_id"],
    )
    op.create_unique_constraint(
        "uq_documents_id_course_user", "documents", ["id", "course_id", "user_id"]
    )
    op.create_unique_constraint(
        "uq_document_revisions_document_id", "document_revisions", ["document_id", "id"]
    )

    op.create_foreign_key(
        "fk_stored_objects_course_user",
        "stored_objects",
        "courses",
        ["course_id", "user_id"],
        ["id", "user_id"],
    )
    op.create_foreign_key(
        "fk_upload_sessions_course_user",
        "upload_sessions",
        "courses",
        ["course_id", "user_id"],
        ["id", "user_id"],
    )
    op.create_foreign_key(
        "fk_upload_sessions_object_scope",
        "upload_sessions",
        "stored_objects",
        ["stored_object_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
    )
    op.create_foreign_key(
        "fk_documents_course_user",
        "documents",
        "courses",
        ["course_id", "user_id"],
        ["id", "user_id"],
    )
    op.create_foreign_key(
        "fk_documents_object_scope",
        "documents",
        "stored_objects",
        ["stored_object_id", "course_id", "user_id"],
        ["id", "course_id", "user_id"],
    )

    op.drop_constraint("fk_documents_preview_revision", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_active_revision", "documents", type_="foreignkey")
    op.create_foreign_key(
        "fk_documents_preview_revision",
        "documents",
        "document_revisions",
        ["id", "preview_revision_id"],
        ["document_id", "id"],
    )
    op.create_foreign_key(
        "fk_documents_active_revision",
        "documents",
        "document_revisions",
        ["id", "active_revision_id"],
        ["document_id", "id"],
    )
    op.create_index(
        "uq_documents_visible_content_role",
        "documents",
        ["course_id", "verified_sha256", "corpus_role"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_visible_content_role", table_name="documents")
    op.drop_constraint("fk_documents_active_revision", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_preview_revision", "documents", type_="foreignkey")
    op.create_foreign_key(
        "fk_documents_preview_revision",
        "documents",
        "document_revisions",
        ["preview_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_documents_active_revision",
        "documents",
        "document_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint("fk_documents_object_scope", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_course_user", "documents", type_="foreignkey")
    op.drop_constraint("fk_upload_sessions_object_scope", "upload_sessions", type_="foreignkey")
    op.drop_constraint("fk_upload_sessions_course_user", "upload_sessions", type_="foreignkey")
    op.drop_constraint("fk_stored_objects_course_user", "stored_objects", type_="foreignkey")
    op.drop_constraint("uq_document_revisions_document_id", "document_revisions", type_="unique")
    op.drop_constraint("uq_documents_id_course_user", "documents", type_="unique")
    op.drop_constraint("uq_stored_objects_id_course_user", "stored_objects", type_="unique")
    op.drop_constraint("uq_courses_id_user", "courses", type_="unique")

    op.drop_constraint("uq_idempotency_actor_operation_key", "idempotency_records", type_="unique")
    op.create_unique_constraint(
        "uq_idempotency_actor_operation_key",
        "idempotency_records",
        ["actor_subject", "operation", "idempotency_key"],
    )
    op.drop_column("idempotency_records", "actor_authentication_method")

    op.drop_constraint("fk_upload_sessions_document", "upload_sessions", type_="foreignkey")
    op.drop_column("upload_sessions", "document_id")
    op.drop_constraint("fk_documents_user", "documents", type_="foreignkey")
    op.drop_column("documents", "user_id")
