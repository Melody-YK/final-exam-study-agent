import pytest
from pydantic import ValidationError

from study_contracts import (
    Asset,
    AssetType,
    BoundingBox,
    Chunk,
    Evidence,
    Note,
    NoteSource,
    SourceLocator,
)


def _source() -> NoteSource:
    return NoteSource(
        id="source-1",
        evidence_id="evidence-1",
        document_id="document-1",
        revision_id="revision-1",
        chunk_id="chunk-1",
        locator=SourceLocator(kind="slide", ordinal=4),
        quote="进程是资源分配的基本单位",
    )


def test_asset_round_trips_with_stable_source_metadata() -> None:
    asset = Asset(
        id="asset-1",
        type=AssetType.RENDERED_PAGE,
        locator=SourceLocator(kind="slide", ordinal=4),
        bbox_norm=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
        object_ref="derived/revision-1/slide-4.png",
        media_type="image/png",
        sha256="a" * 64,
        source_backend="libreoffice",
        source_version="24.2",
        raw_result_ref="raw/revision-1/slide-4.json",
    )

    restored = Asset.model_validate_json(asset.model_dump_json())

    assert restored == asset
    assert restored.schema_version == "1.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", "2.0"), ("sha256", "not-a-sha256"), ("media_type", "image")],
)
def test_asset_rejects_unstable_or_malformed_metadata(field: str, value: str) -> None:
    values = {
        "id": "asset-1",
        "type": AssetType.IMAGE,
        "locator": SourceLocator(kind="page", ordinal=1),
        "bbox_norm": BoundingBox(x=0.1, y=0.1, width=0.2, height=0.2),
        "object_ref": "derived/image.png",
        "media_type": "image/png",
        "sha256": "a" * 64,
        "source_backend": "pdf-native",
        "source_version": "1.0",
        "raw_result_ref": "raw/page-1.json",
        field: value,
    }

    with pytest.raises(ValidationError):
        Asset(**values)


def test_chunk_rejects_blank_text_and_duplicate_source_blocks() -> None:
    base = {
        "id": "chunk-1",
        "revision_id": "revision-1",
        "text": "调度决定下一个运行进程。",
        "locator": SourceLocator(kind="slide", ordinal=8),
        "section_path": ["进程管理", "调度"],
        "source_block_ids": ["block-1"],
        "token_count_estimate": 12,
        "content_sha256": "b" * 64,
    }

    with pytest.raises(ValidationError):
        Chunk(**{**base, "text": "   "})
    with pytest.raises(ValidationError):
        Chunk(**{**base, "source_block_ids": ["block-1", "block-1"]})


def test_evidence_round_trips_without_losing_scope_or_location() -> None:
    evidence = Evidence(
        id="evidence-1",
        course_id="course-1",
        document_id="document-1",
        revision_id="revision-1",
        chunk_id="chunk-1",
        text="进程是资源分配的基本单位。",
        content_sha256="c" * 64,
        locator=SourceLocator(kind="slide", ordinal=4),
        bounding_boxes=[BoundingBox(x=0.1, y=0.2, width=0.5, height=0.1)],
    )

    restored = Evidence.model_validate_json(evidence.model_dump_json())

    assert restored == evidence
    assert restored.schema_version == "1.0"


def test_model_generated_note_requires_unique_sources() -> None:
    values = {
        "id": "note-1",
        "course_id": "course-1",
        "section_path": ["进程管理"],
        "title": "进程基础",
        "body_markdown": "进程是资源分配的基本单位。",
        "version": 1,
        "generated_by_model": True,
    }

    with pytest.raises(ValidationError):
        Note(**values)
    with pytest.raises(ValidationError):
        Note(**values, sources=[_source(), _source()])

    note = Note(**values, sources=[_source()])
    restored = Note.model_validate_json(note.model_dump_json())

    assert restored == note
    assert restored.sources[0].schema_version == "1.0"
