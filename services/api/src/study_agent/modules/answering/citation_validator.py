"""Server-side validation for model-proposed claims and citations."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from pydantic import ValidationError

from study_agent.modules.answering.types import AuthorizedEvidence
from study_contracts import AnswerStatus, StructuredAnswer


class CitationValidationError(ValueError):
    """The provider referred to evidence outside the authorized snapshot."""


class AnswerPayloadError(ValueError):
    """The provider payload does not match the structured answer contract."""


def _searchable_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(normalized.split()).casefold()


class CitationValidator:
    def validate(
        self,
        *,
        query_id: str,
        payload: Mapping[str, object],
        authorized: tuple[AuthorizedEvidence, ...],
    ) -> StructuredAnswer:
        canonical_payload = dict(payload)
        canonical_payload["query_id"] = query_id
        canonical_payload.setdefault("schema_version", "1.0")
        try:
            answer = StructuredAnswer.model_validate(canonical_payload)
        except ValidationError as exc:
            raise AnswerPayloadError("provider answer schema is invalid") from exc

        if answer.status is AnswerStatus.ABSTAINED:
            return answer

        by_id = {item.evidence.id: item for item in authorized}
        if len(by_id) != len(authorized):
            raise CitationValidationError("authorized evidence identifiers are not unique")

        referenced = {citation_id for claim in answer.claims for citation_id in claim.citation_ids}
        if referenced != {citation.id for citation in answer.citations}:
            raise CitationValidationError("citations must be used by at least one claim")

        for citation in answer.citations:
            source = by_id.get(citation.id)
            if source is None:
                raise CitationValidationError("citation was not in the authorized candidate set")
            evidence = source.evidence
            if not source.provenance or any(not item.strip() for item in source.provenance):
                raise CitationValidationError("citation evidence lacks verified provenance")
            if citation.document_id != evidence.document_id:
                raise CitationValidationError("citation document does not match evidence")
            if citation.revision_id != evidence.revision_id:
                raise CitationValidationError("citation revision does not match evidence")
            if citation.chunk_id != evidence.chunk_id:
                raise CitationValidationError("citation chunk does not match evidence")
            if citation.document_name != source.document_name:
                raise CitationValidationError("citation document name does not match evidence")
            if citation.locator != evidence.locator:
                raise CitationValidationError("citation locator does not match evidence")
            if citation.bounding_boxes != evidence.bounding_boxes:
                raise CitationValidationError("citation bounding boxes do not match evidence")

            quote = _searchable_text(citation.quote)
            source_text = _searchable_text(evidence.text)
            if not quote or quote not in source_text:
                raise CitationValidationError("citation quote does not occur in evidence")
        return answer
