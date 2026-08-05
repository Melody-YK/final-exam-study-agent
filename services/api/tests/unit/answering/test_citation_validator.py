import pytest

from study_agent.modules.answering.citation_validator import (
    CitationValidationError,
    CitationValidator,
)
from study_agent.modules.answering.types import AuthorizedEvidence
from study_contracts import BoundingBox, Evidence, SourceLocator


def _authorized(*, provenance: tuple[str, ...] = ("pdf-native@1.0",)) -> AuthorizedEvidence:
    return AuthorizedEvidence(
        evidence=Evidence(
            id="evidence-1",
            course_id="course-1",
            document_id="document-1",
            revision_id="revision-1",
            chunk_id="chunk-1",
            text="进程是资源分配的基本单位。线程是调度的基本单位。",
            content_sha256="b" * 64,
            locator=SourceLocator(kind="page", ordinal=12),
            bounding_boxes=[BoundingBox(x=0.1, y=0.2, width=0.5, height=0.1)],
        ),
        document_name="操作系统.pdf",
        score=0.91,
        document_deletion_epoch=0,
        provenance=provenance,
    )


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "answered",
        "answer_markdown": "进程是资源分配的基本单位。",
        "claims": [
            {
                "id": "claim-1",
                "text": "进程是资源分配的基本单位。",
                "citation_ids": ["evidence-1"],
            }
        ],
        "citations": [
            {
                "id": "evidence-1",
                "document_id": "document-1",
                "revision_id": "revision-1",
                "chunk_id": "chunk-1",
                "document_name": "操作系统.pdf",
                "locator": {"kind": "page", "ordinal": 12},
                "quote": "进程是资源分配的基本单位",
                "bounding_boxes": [{"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1}],
            }
        ],
    }


def test_validator_accepts_only_canonical_authorized_source_metadata() -> None:
    answer = CitationValidator().validate(
        query_id="query-1",
        payload=_payload(),
        authorized=(_authorized(),),
    )

    assert answer.query_id == "query-1"
    assert answer.citations[0].quote == "进程是资源分配的基本单位"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_id", "forged-document"),
        ("revision_id", "old-revision"),
        ("chunk_id", "other-chunk"),
        ("document_name", "forged.pdf"),
        ("locator", {"kind": "page", "ordinal": 99}),
        ("bounding_boxes", [{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}]),
    ],
)
def test_validator_rejects_forged_citation_metadata(field: str, value: object) -> None:
    payload = _payload()
    citation = payload["citations"][0]  # type: ignore[index]
    citation[field] = value  # type: ignore[index]

    with pytest.raises(CitationValidationError):
        CitationValidator().validate(
            query_id="query-1",
            payload=payload,
            authorized=(_authorized(),),
        )


def test_validator_rejects_unknown_ids_quote_mismatches_and_missing_provenance() -> None:
    unknown = _payload()
    unknown["citations"][0]["id"] = "not-retrieved"  # type: ignore[index]
    unknown["claims"][0]["citation_ids"] = ["not-retrieved"]  # type: ignore[index]
    quote_mismatch = _payload()
    quote_mismatch["citations"][0]["quote"] = "来自模型外部知识"  # type: ignore[index]

    validator = CitationValidator()
    with pytest.raises(CitationValidationError):
        validator.validate(query_id="query-1", payload=unknown, authorized=(_authorized(),))
    with pytest.raises(CitationValidationError):
        validator.validate(query_id="query-1", payload=quote_mismatch, authorized=(_authorized(),))
    with pytest.raises(CitationValidationError):
        validator.validate(
            query_id="query-1",
            payload=_payload(),
            authorized=(_authorized(provenance=()),),
        )


def test_evidence_validator_rejects_general_knowledge_bypass() -> None:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "status": "answered",
        "answer_basis": "ai_general_knowledge",
        "answer_markdown": "这是没有课程来源的模型回答。",
        "claims": [],
        "citations": [],
    }

    with pytest.raises(CitationValidationError):
        CitationValidator().validate(
            query_id="query-1",
            payload=payload,
            authorized=(_authorized(),),
        )
