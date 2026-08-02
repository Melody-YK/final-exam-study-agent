from __future__ import annotations

import pytest

from study_agent.infrastructure.db.models import NoteCoverageUnitModel
from study_agent.modules.notes.demo_runner import (
    _FrozenInput,
    _Material,
    _render_model_note,
    _SourceChunk,
)
from study_agent.modules.notes.service import NoteSourceSnapshot, _knowledge_points
from study_agent.providers.protocols import StructuredJsonDraft
from study_contracts import NoteBatchStyle, SourceLocator


def _material() -> _Material:
    unit = NoteCoverageUnitModel(
        id="unit-1",
        input_id="input-1",
        batch_id="batch-1",
        user_id="user-1",
        course_id="course-1",
        ordinal=1,
        unit_type="pdf_page_window",
        locator="page:1",
        content_sha256="a" * 64,
        is_substantive=True,
    )
    chunk = _SourceChunk(
        input_id="input-1",
        document_id="document-1",
        revision_id="revision-1",
        deletion_epoch=0,
        document_name="操作系统.pdf",
        media_type="application/pdf",
        chunk_id="chunk-1",
        ordinal=1,
        text="进程是操作系统进行资源分配的基本单位。",
        page_ordinal=1,
        content_sha256="b" * 64,
    )
    return _Material(
        title="操作系统复习",
        style=NoteBatchStyle.EXAM_FOCUS,
        section_path=["期末复习"],
        inputs=(
            _FrozenInput(
                input_id="input-1",
                document_id="document-1",
                revision_id="revision-1",
                deletion_epoch=0,
                content_sha256="c" * 64,
            ),
        ),
        chunks=(chunk,),
        units=(unit,),
    )


def _response(*, evidence_id: str = "chunk-1") -> StructuredJsonDraft:
    return StructuredJsonDraft(
        payload={
            "schema_version": "1.0",
            "title": "进程基础",
            "body_markdown": "进程是资源分配的基本单位。",
            "claims": [
                {
                    "id": "claim-1",
                    "text": "进程承担资源分配职责。",
                    "citation_ids": ["cite-1"],
                }
            ],
            "citations": [
                {
                    "id": "cite-1",
                    "evidence_id": evidence_id,
                    "coverage_unit_ids": ["unit-1"],
                }
            ],
            "coverage_unit_refs": ["unit-1"],
            "content_ast": {
                "schema_version": "1.0",
                "nodes": [
                    {
                        "id": "body-1",
                        "type": "paragraph",
                        "text": "进程是资源分配的基本单位。",
                        "provenance": "source_backed",
                    }
                ],
            },
        },
        model="deepseek-v4-flash",
        usage={"input_tokens": 30, "output_tokens": 20, "total_tokens": 50},
    )


def test_model_note_keeps_ai_body_separate_from_source_mapping() -> None:
    rendered = _render_model_note(_material(), _response())

    assert rendered.generated_by_model is True
    assert rendered.title == "进程基础"
    assert rendered.provider_alias == "deepseek-chat"
    assert rendered.body_markdown == "进程是资源分配的基本单位。"
    assert "来源对应" not in rendered.body_markdown
    assert "操作系统.pdf" not in rendered.body_markdown
    assert rendered.entries[0].chunk.chunk_id == "chunk-1"
    assert rendered.content_ast["nodes"][-1]["type"] == "paragraph"
    assert rendered.content_ast["nodes"][-1]["text"] == "进程承担资源分配职责。"
    assert rendered.content_ast["nodes"][-1]["children"][0]["citation_id"] == "chunk-1"


def test_model_note_rejects_unknown_evidence_reference() -> None:
    with pytest.raises(ValueError, match="unknown evidence"):
        _render_model_note(_material(), _response(evidence_id="missing"))


def test_model_note_rebuilds_nonportable_ast_from_valid_claims() -> None:
    response = _response()
    response.payload.pop("content_ast")

    rendered = _render_model_note(_material(), response)

    assert rendered.generated_by_model is True
    assert rendered.content_ast["nodes"][0]["id"] == "model-title"


def test_model_note_accepts_common_assertion_and_citations_claim_aliases() -> None:
    response = _response()
    response.payload["claims"] = [
        {"id": "claim-1", "assertion": "进程承担资源分配职责。", "citations": ["cite-1"]}
    ]

    rendered = _render_model_note(_material(), response)

    assert rendered.body_markdown == "进程是资源分配的基本单位。"
    assert rendered.content_ast["nodes"][-1]["text"] == "进程承担资源分配职责。"


def test_model_note_normalizes_answer_style_citations() -> None:
    response = _response()
    response.payload["claims"] = [
        {
            "id": "claim-1",
            "assertion": "进程承担资源分配职责。",
            "citations": [{"id": "passage-1", "chunk_id": "chunk-1"}],
        }
    ]
    response.payload["citations"] = [{"id": "passage-1", "chunk_id": "chunk-1"}]

    rendered = _render_model_note(_material(), response)

    assert rendered.entries[0].chunk.chunk_id == "chunk-1"


def test_legacy_claims_project_back_to_visible_body_blocks() -> None:
    source = NoteSourceSnapshot(
        id="source-1",
        evidence_id="chunk-1",
        document_id="document-1",
        revision_id="revision-1",
        chunk_id="chunk-1",
        document_name="操作系统.pdf",
        locator=SourceLocator(kind="page", ordinal=3),
        quote="高级调度、低级调度和中级调度",
        bounding_boxes=(),
        provenance=("native",),
        available=True,
        stale=False,
        unavailable_reason=None,
    )
    ast = {
        "nodes": [
            {
                "id": "legacy-claim",
                "type": "paragraph",
                "text": "处理机调度分为高级调度、低级调度和中级调度。",
                "children": [{"type": "citation", "citation_id": "chunk-1"}],
            }
        ]
    }
    body = """## 处理机调度

### 调度层次
- **高级调度**、**低级调度**和**中级调度**负责不同层次的资源管理。

### 来源对应
- 处理机调度分为高级调度、低级调度和中级调度。 (来源: 操作系统.pdf · 第 3 页)
"""

    points = _knowledge_points(ast, (source,), body)

    assert len(points) == 1
    assert points[0].text == "高级调度、低级调度和中级调度负责不同层次的资源管理。"
    assert points[0].source_ids == ("source-1",)
