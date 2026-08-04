"""Build structured prompts without promoting retrieved text to instructions."""

from __future__ import annotations

from study_agent.modules.answering.types import AuthorizedEvidence
from study_agent.providers.protocols import (
    ConversationContextTurn,
    EvidencePrompt,
    LearnerMemoryContext,
    Passage,
)


def build_evidence_prompt(
    question: str,
    candidates: tuple[AuthorizedEvidence, ...],
    *,
    conversation_context: tuple[ConversationContextTurn, ...] = (),
    conversation_summary: str | None = None,
    learner_memories: tuple[LearnerMemoryContext, ...] = (),
    standalone_question: str | None = None,
) -> EvidencePrompt:
    normalized_question = question.strip()
    if not normalized_question:
        raise ValueError("question must not be blank")
    if not candidates:
        raise ValueError("an evidence prompt requires authorized candidates")

    passages = tuple(
        Passage(
            id=item.evidence.id,
            text=item.evidence.text,
            metadata={
                "trust_boundary": "untrusted_evidence",
                "course_id": item.evidence.course_id,
                "document_id": item.evidence.document_id,
                "revision_id": item.evidence.revision_id,
                "chunk_id": item.evidence.chunk_id,
                "content_sha256": item.evidence.content_sha256,
                "locator": item.evidence.locator.model_dump(mode="json"),
                "bounding_boxes": [
                    box.model_dump(mode="json") for box in item.evidence.bounding_boxes
                ],
                "document_name": item.document_name,
                "provenance": list(item.provenance),
            },
        )
        for item in candidates
    )
    return EvidencePrompt(
        query=normalized_question,
        passages=passages,
        conversation_context=conversation_context,
        conversation_summary=conversation_summary,
        learner_memories=learner_memories,
        standalone_question=(
            standalone_question.strip()
            if standalone_question is not None
            and standalone_question.strip() != normalized_question
            else None
        ),
    )
