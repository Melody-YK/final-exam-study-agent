import pytest

from study_agent.modules.courses.upload_validation import (
    MARKDOWN_MEDIA_TYPE,
    MAX_MARKDOWN_UPLOAD_BYTES,
    UploadRejected,
    UploadRejectionReason,
    UploadValidator,
)


@pytest.mark.parametrize(
    ("filename", "content_type", "payload", "expected_type"),
    [
        ("chapter.pdf", "application/pdf", b"%PDF-1.7\n", "application/pdf"),
        ("outline.md", MARKDOWN_MEDIA_TYPE, "# 复习提纲".encode(), MARKDOWN_MEDIA_TYPE),
        (
            "outline.markdown",
            MARKDOWN_MEDIA_TYPE,
            b"\xef\xbb\xbf# Review outline",
            MARKDOWN_MEDIA_TYPE,
        ),
        ("photo.png", "image/png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0", "image/jpeg"),
    ],
)
def test_upload_validator_accepts_supported_content(
    filename: str, content_type: str, payload: bytes, expected_type: str
) -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    result = validator.validate(filename, content_type, payload)

    assert result.media_type == expected_type
    assert len(result.sha256) == 64


def test_upload_validator_rejects_extension_and_magic_mismatch() -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    with pytest.raises(UploadRejected, match="文件内容与声明的类型不匹配"):
        validator.validate("chapter.pdf", "application/pdf", b"not a pdf")


def test_upload_validator_rejects_oversized_payload_before_parsing() -> None:
    validator = UploadValidator(max_upload_bytes=4)

    with pytest.raises(UploadRejected, match="size"):
        validator.validate("chapter.pdf", "application/pdf", b"%PDF-too-large")


def test_upload_validator_applies_a_smaller_markdown_limit() -> None:
    validator = UploadValidator(max_upload_bytes=100 * 1024 * 1024)

    with pytest.raises(UploadRejected, match="Markdown 单个文件不能超过 5 MB") as caught:
        validator.validate_declaration(
            "outline.md",
            MARKDOWN_MEDIA_TYPE,
            MAX_MARKDOWN_UPLOAD_BYTES + 1,
            "a" * 64,
        )

    assert caught.value.reason is UploadRejectionReason.TOO_LARGE


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        (
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        (
            "notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("scan.tiff", "image/tiff"),
    ],
)
def test_upload_validator_rejects_formats_that_require_conversion(
    filename: str, content_type: str
) -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    with pytest.raises(UploadRejected, match="请先转换为 PDF 或 Markdown") as caught:
        validator.validate(filename, content_type, b"unsupported")

    assert caught.value.reason is UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"# valid prefix\n\n\x00binary tail", "NUL"),
        (b"# valid prefix\n\n\xff", "UTF-8"),
        (b" \r\n\t ", "不能为空"),
    ],
)
def test_upload_validator_rejects_invalid_markdown_content(payload: bytes, message: str) -> None:
    validator = UploadValidator(max_upload_bytes=1024)

    with pytest.raises(UploadRejected, match=message) as caught:
        validator.validate("outline.md", MARKDOWN_MEDIA_TYPE, payload)

    assert caught.value.reason is UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE
