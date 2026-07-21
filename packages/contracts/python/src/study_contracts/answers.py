"""Versioned, evidence-bound answer contracts."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from study_contracts.documents import BoundingBox, ContractModel, SourceLocator


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    ABSTAINED = "abstained"


class Claim(ContractModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_citation_ids(self) -> Self:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("claim citation_ids must be unique")
        if any(not citation_id for citation_id in self.citation_ids):
            raise ValueError("claim citation_ids must not contain empty identifiers")
        return self


class Citation(ContractModel):
    id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    document_name: str = Field(min_length=1)
    locator: SourceLocator
    quote: str = Field(min_length=1)
    bounding_boxes: list[BoundingBox] = Field(default_factory=list)


class Refusal(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class StructuredAnswer(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    query_id: str = Field(min_length=1)
    status: AnswerStatus
    answer_markdown: str
    claims: list[Claim] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    refusal: Refusal | None = None

    @model_validator(mode="after")
    def validate_status_invariants(self) -> Self:
        claim_ids = [claim.id for claim in self.claims]
        citation_ids = [citation.id for citation in self.citations]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim identifiers must be unique")
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation identifiers must be unique")

        if self.status is AnswerStatus.ABSTAINED:
            if self.answer_markdown.strip():
                raise ValueError("abstained answers must not include answer text")
            if self.claims or self.citations:
                raise ValueError("abstained answers must not include claims or citations")
            if self.refusal is None:
                raise ValueError("abstained answers require a refusal")
            return self

        if not self.answer_markdown.strip():
            raise ValueError("answered responses require answer text")
        if not self.claims or not self.citations:
            raise ValueError("answered responses require claims and citations")
        if self.refusal is not None:
            raise ValueError("answered responses must not include a refusal")

        known_citation_ids = set(citation_ids)
        for claim in self.claims:
            if not claim.citation_ids:
                raise ValueError("every answered claim requires at least one citation")
            unknown_ids = set(claim.citation_ids) - known_citation_ids
            if unknown_ids:
                raise ValueError("claim references citation identifiers not present in citations")
        return self
