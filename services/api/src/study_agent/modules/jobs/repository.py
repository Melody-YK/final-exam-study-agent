"""PostgreSQL queries that enforce job scope and claim serialization."""

from datetime import datetime
from typing import cast

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from study_agent.infrastructure.db.models import (
    DocumentModel,
    PageCheckpointModel,
    ParseJobModel,
    StoredObjectModel,
)
from study_contracts import WorkerCapabilities


class JobRepository:
    async def remaining_requested_pages(
        self,
        session: AsyncSession,
        job: ParseJobModel,
    ) -> list[int]:
        successful_pages = set(
            (
                await session.scalars(
                    select(PageCheckpointModel.page_ordinal)
                    .where(
                        PageCheckpointModel.job_id == job.id,
                        PageCheckpointModel.status == "succeeded",
                    )
                    .distinct()
                )
            ).all()
        )
        if job.requested_pages:
            original_pages = set(job.requested_pages)
        else:
            total_pages = job.progress.get("total_pages")
            original_pages = (
                set(range(1, total_pages + 1))
                if isinstance(total_pages, int) and total_pages > 0
                else set()
            )
        return sorted(original_pages - successful_pages)

    async def claim_candidate(
        self,
        session: AsyncSession,
        capabilities: WorkerCapabilities,
        now: datetime,
    ) -> ParseJobModel | None:
        available = or_(
            and_(
                ParseJobModel.status.in_(("queued", "retry_wait")),
                ParseJobModel.available_at <= now,
            ),
            and_(
                ParseJobModel.status.in_(("leased", "parsing")),
                ParseJobModel.lease_expires_at.is_not(None),
                ParseJobModel.lease_expires_at <= now,
            ),
        )
        statement = (
            select(ParseJobModel)
            .join(
                DocumentModel,
                and_(
                    DocumentModel.id == ParseJobModel.document_id,
                    DocumentModel.course_id == ParseJobModel.course_id,
                    DocumentModel.user_id == ParseJobModel.user_id,
                ),
            )
            .join(
                StoredObjectModel,
                and_(
                    StoredObjectModel.id == ParseJobModel.stored_object_id,
                    StoredObjectModel.course_id == ParseJobModel.course_id,
                    StoredObjectModel.user_id == ParseJobModel.user_id,
                ),
            )
            .where(
                available,
                DocumentModel.deleted_at.is_(None),
                DocumentModel.deletion_epoch == ParseJobModel.document_deletion_epoch,
                StoredObjectModel.deleted_at.is_(None),
                ParseJobModel.job_type == "parse",
                ParseJobModel.parser_profile.in_(capabilities.parser_profiles),
                ParseJobModel.media_type.in_(capabilities.media_types),
                ParseJobModel.input_size_bytes <= capabilities.max_input_bytes,
                or_(
                    ParseJobModel.estimated_pages.is_(None),
                    ParseJobModel.estimated_pages <= capabilities.max_pages,
                ),
                or_(
                    ParseJobModel.requires_ocr.is_(False),
                    literal(capabilities.supports_ocr),
                ),
                or_(
                    ParseJobModel.requires_rendering.is_(False),
                    literal(capabilities.supports_rendering),
                ),
            )
            .order_by(ParseJobModel.available_at, ParseJobModel.created_at, ParseJobModel.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=ParseJobModel)
        )
        return cast(ParseJobModel | None, await session.scalar(statement))

    async def worker_job_for_update(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> ParseJobModel | None:
        return cast(
            ParseJobModel | None,
            await session.scalar(
                select(ParseJobModel)
                .where(ParseJobModel.id == job_id)
                .with_for_update(of=ParseJobModel)
            ),
        )
