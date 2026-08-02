"""HTTP contracts for administrator learning-content inspection."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AdminCourseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    lifecycle: str
    owner_account_id: str | None
    owner_email: str | None
    owner_display_name: str | None
    owner_subject: str
    document_count: int = Field(ge=0)
    note_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class AdminCoursesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminCourseResponse]


class AdminNoteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    course_id: str
    section_path: list[str]
    title: str
    body_markdown: str
    version: int = Field(ge=1)
    generation: int = Field(ge=1)
    generated_by_model: bool
    status: str
    created_at: datetime
    updated_at: datetime


class AdminNotesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdminNoteResponse]


__all__ = [
    "AdminCourseResponse",
    "AdminCoursesResponse",
    "AdminNoteResponse",
    "AdminNotesResponse",
]
