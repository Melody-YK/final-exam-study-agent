import pytest

from study_agent.modules.courses.upload_validation import (
    UploadRejected,
    UploadValidator,
)

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.parametrize(
    ("filename", "content_type", "payload", "expected_type"),
    [
        ("chapter.pdf", "application/pdf", b"%PDF-1.7\n", "application/pdf"),
        ("slides.pptx", PPTX_MEDIA_TYPE, b"PK\x03\x04data", PPTX_MEDIA_TYPE),
        ("photo.png", "image/png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0", "image/jpeg"),
    ],
)
def test_upload_validator_accepts_supported_magic_bytes(
    filename: str, content_type: str, payload: bytes, expected_type: str
) -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    result = validator.validate(filename, content_type, payload)

    assert result.media_type == expected_type
    assert len(result.sha256) == 64


def test_upload_validator_rejects_extension_and_magic_mismatch() -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    with pytest.raises(UploadRejected, match="magic"):
        validator.validate("chapter.pdf", "application/pdf", b"not a pdf")


def test_upload_validator_rejects_oversized_payload_before_parsing() -> None:
    validator = UploadValidator(max_upload_bytes=4)

    with pytest.raises(UploadRejected, match="size"):
        validator.validate("chapter.pdf", "application/pdf", b"%PDF-too-large")
