import pytest
from pydantic import ValidationError

from study_contracts.answers import (
    AnswerBasis,
    AnswerStatus,
    Citation,
    Claim,
    Refusal,
    StructuredAnswer,
)
from study_contracts.documents import BoundingBox, SourceLocator


def test_answered_response_requires_claim_citations() -> None:
    with pytest.raises(ValidationError):
        StructuredAnswer(
            query_id="query-1",
            status=AnswerStatus.ANSWERED,
            answer_markdown="进程是资源分配的基本单位。",
            claims=[Claim(id="claim-1", text="进程是资源分配的基本单位。")],
            citations=[],
        )


def test_answered_response_round_trips_claim_and_source_location() -> None:
    answer = StructuredAnswer(
        query_id="query-1",
        status=AnswerStatus.ANSWERED,
        answer_markdown="进程是资源分配的基本单位。",
        claims=[
            Claim(
                id="claim-1",
                text="进程是资源分配的基本单位。",
                citation_ids=["citation-1"],
            )
        ],
        citations=[
            Citation(
                id="citation-1",
                document_id="document-1",
                revision_id="revision-1",
                chunk_id="chunk-1",
                document_name="第2章 进程管理.pptx",
                locator=SourceLocator(kind="slide", ordinal=12),
                quote="进程是资源分配的基本单位",
                bounding_boxes=[BoundingBox(x=0.1, y=0.2, width=0.5, height=0.1)],
            )
        ],
    )

    restored = StructuredAnswer.model_validate_json(answer.model_dump_json())

    assert restored == answer
    assert restored.citations[0].locator.ordinal == 12


def test_general_answer_is_marked_and_cannot_claim_course_citations() -> None:
    answer = StructuredAnswer(
        query_id="query-general",
        status=AnswerStatus.ANSWERED,
        answer_markdown="可以把进程理解成一个运行中的程序实例。",
        answer_basis=AnswerBasis.AI_GENERAL_KNOWLEDGE,
    )

    restored = StructuredAnswer.model_validate_json(answer.model_dump_json())

    assert restored.answer_basis is AnswerBasis.AI_GENERAL_KNOWLEDGE
    with pytest.raises(ValidationError):
        StructuredAnswer(
            query_id="query-general",
            status=AnswerStatus.ANSWERED,
            answer_markdown="无来源回答",
            answer_basis=AnswerBasis.AI_GENERAL_KNOWLEDGE,
            claims=[Claim(id="claim-1", text="无来源", citation_ids=["citation-1"])],
        )


def test_abstained_response_requires_refusal_and_forbids_claims() -> None:
    answer = StructuredAnswer(
        query_id="query-2",
        status=AnswerStatus.ABSTAINED,
        answer_markdown="",
        refusal=Refusal(
            code="INSUFFICIENT_EVIDENCE",
            message="当前课件中没有足够依据回答该问题。",
        ),
    )

    assert answer.refusal is not None

    with pytest.raises(ValidationError):
        StructuredAnswer(
            query_id="query-2",
            status=AnswerStatus.ABSTAINED,
            answer_markdown="猜测答案",
            claims=[Claim(id="claim-1", text="无来源猜测", citation_ids=["missing"])],
            refusal=answer.refusal,
        )
