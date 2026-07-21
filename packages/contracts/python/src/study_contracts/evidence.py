"""Versioned evidence contracts used between retrieval and answering."""

from typing import Literal

from pydantic import Field

from study_contracts.documents import (
    BoundingBox,
    ContractModel,
    NonEmptyString,
    Sha256Hex,
    SourceLocator,
)


class Evidence(ContractModel):
    """An authorized, immutable chunk snapshot offered to an answer provider."""

    schema_version: Literal["1.0"] = "1.0"
    id: NonEmptyString
    course_id: NonEmptyString
    document_id: NonEmptyString
    revision_id: NonEmptyString
    chunk_id: NonEmptyString
    text: NonEmptyString
    content_sha256: Sha256Hex
    locator: SourceLocator
    bounding_boxes: list[BoundingBox] = Field(default_factory=list)
