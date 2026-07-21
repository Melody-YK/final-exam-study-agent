"""Versioned note and note-source contracts."""

from typing import Literal, Self

from pydantic import Field, model_validator

from study_contracts.documents import ContractModel, NonEmptyString, SourceLocator


class NoteSource(ContractModel):
    """A persisted source relationship independent from editable note text."""

    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    evidence_id: NonEmptyString
    document_id: NonEmptyString
    revision_id: NonEmptyString
    chunk_id: NonEmptyString
    locator: SourceLocator
    quote: NonEmptyString


class Note(ContractModel):
    """An editable section note with optimistic-lock and source metadata."""

    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    course_id: NonEmptyString
    section_path: list[NonEmptyString] = Field(min_length=1)
    title: NonEmptyString
    body_markdown: NonEmptyString
    version: int = Field(ge=1)
    generated_by_model: bool = False
    sources: list[NoteSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("note source identifiers must be unique")
        if self.generated_by_model and not self.sources:
            raise ValueError("model-generated notes require at least one source")
        return self
