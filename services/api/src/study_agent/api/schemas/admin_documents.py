"""HTTP contracts for administrator document review."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from study_agent.modules.courses.documents import DocumentReviewStatus

type DocumentReviewDecision = Literal["approved", "rejected"]


class AdminDocumentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    course_id: str
    course_title: str
    owner_account_id: str | None
    owner_email: str | None
    owner_display_name: str | None
    owner_subject: str
    filename: str
    media_type: str
    size_bytes: int = Field(ge=0)
    corpus_role: str
    status: str
    page_count: int | None = Field(default=None, ge=1)
    review_status: DocumentReviewStatus
    review_note: str | None
    reviewed_by_account_id: str | None
    reviewed_by_email: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminDocumentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminDocumentResponse]


class AdminDocumentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: DocumentReviewDecision
    review_note: str | None = Field(default=None, max_length=500)

    @field_validator("review_note")
    @classmethod
    def normalize_review_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def rejected_document_requires_note(self) -> Self:
        if self.review_status == "rejected" and self.review_note is None:
            raise ValueError("rejected documents require a review note")
        return self


__all__ = [
    "AdminDocumentResponse",
    "AdminDocumentReviewRequest",
    "AdminDocumentsResponse",
    "DocumentReviewDecision",
    "DocumentReviewStatus",
]
