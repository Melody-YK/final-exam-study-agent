import pytest
from pydantic import ValidationError

from study_contracts.documents import (
    Block,
    BlockType,
    BoundingBox,
    Page,
    ParseResultManifest,
)


def test_parse_manifest_round_trips_with_normalized_source_metadata() -> None:
    manifest = ParseResultManifest(
        document_sha256="a" * 64,
        parser_profile="pptx-native-v1",
        pages=[
            Page(
                ordinal=1,
                width=1920,
                height=1080,
                source_kind="slide",
                source_backend="pptx-native",
                source_version="1.0.2",
                raw_result_ref="raw/slide-1.json",
                blocks=[
                    Block(
                        id="block-1",
                        type=BlockType.TITLE,
                        text="进程与线程",
                        bbox_norm=BoundingBox(x=0.1, y=0.2, width=0.7, height=0.1),
                        reading_order=0,
                        confidence=1.0,
                        source_backend="pptx-native",
                        source_version="1.0.2",
                    )
                ],
            )
        ],
    )

    restored = ParseResultManifest.model_validate_json(manifest.model_dump_json())

    assert restored == manifest
    assert restored.schema_version == "1.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [("x", -0.01), ("y", 1.01), ("width", 1.01), ("height", 1.01)],
)
def test_bounding_box_rejects_values_outside_normalized_page(field: str, value: float) -> None:
    values = {"x": 0.0, "y": 0.0, "width": 0.5, "height": 0.5, field: value}

    with pytest.raises(ValidationError):
        BoundingBox(**values)


def test_bounding_box_rejects_coordinates_that_overflow_page() -> None:
    with pytest.raises(ValidationError):
        BoundingBox(x=0.8, y=0.0, width=0.3, height=0.2)


def test_manifest_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        ParseResultManifest(
            schema_version="2.0",
            document_sha256="a" * 64,
            parser_profile="native",
            pages=[],
        )
