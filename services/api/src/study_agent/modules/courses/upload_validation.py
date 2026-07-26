from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePath
from typing import ClassVar

from study_agent.providers.protocols import ObjectMetadata

MARKDOWN_MEDIA_TYPE = "text/markdown"
MAX_MARKDOWN_UPLOAD_BYTES = 5 * 1024 * 1024


class UploadRejectionReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    TOO_LARGE = "too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    SIZE_MISMATCH = "size_mismatch"
    HASH_MISMATCH = "hash_mismatch"


class UploadRejected(ValueError):
    """Raised before an untrusted upload reaches a parser."""

    def __init__(self, message: str, reason: UploadRejectionReason) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    filename: str
    media_type: str
    size_bytes: int
    sha256: str


class UploadValidator:
    _allowed: ClassVar[dict[str, tuple[str, tuple[bytes, ...] | None]]] = {
        ".pdf": ("application/pdf", (b"%PDF-",)),
        ".md": (MARKDOWN_MEDIA_TYPE, None),
        ".markdown": (MARKDOWN_MEDIA_TYPE, None),
        ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
        ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
        ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    }

    def __init__(self, max_upload_bytes: int) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max upload size must be positive")
        self._max_upload_bytes = max_upload_bytes

    def validate(self, filename: str, content_type: str, payload: bytes) -> ValidatedUpload:
        upload = self.validate_declaration(
            filename,
            content_type,
            len(payload),
            sha256(payload).hexdigest(),
        )
        self._validate_content(upload.media_type, payload)
        return upload

    def validate_declaration(
        self,
        filename: str,
        content_type: str,
        size_bytes: int,
        expected_sha256: str,
    ) -> ValidatedUpload:
        safe_name = PurePath(filename).name
        if (
            not safe_name
            or safe_name != filename
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
            raise UploadRejected(
                "filename must not contain a path",
                UploadRejectionReason.INVALID_REQUEST,
            )
        if size_bytes <= 0:
            raise UploadRejected(
                "upload size must be positive",
                UploadRejectionReason.INVALID_REQUEST,
            )
        if size_bytes > self._max_upload_bytes:
            raise UploadRejected(
                "upload size exceeds configured limit",
                UploadRejectionReason.TOO_LARGE,
            )

        extension = PurePath(safe_name).suffix.lower()
        expected = self._allowed.get(extension)
        if expected is None:
            raise UploadRejected(
                "仅支持 PDF、Markdown、JPG 和 PNG; PPTX、DOCX、TIFF 等请先转换为 PDF 或 Markdown。",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
        expected_type, _ = expected
        if content_type.lower().strip() != expected_type:
            raise UploadRejected(
                f"文件类型声明与扩展名不匹配; {safe_name} 应使用 {expected_type}。",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
        if expected_type == MARKDOWN_MEDIA_TYPE and size_bytes > MAX_MARKDOWN_UPLOAD_BYTES:
            raise UploadRejected(
                "Markdown 单个文件不能超过 5 MB; 请拆分章节或转换为 PDF 后重新上传。",
                UploadRejectionReason.TOO_LARGE,
            )

        normalized_sha256 = expected_sha256.lower().strip()
        if len(normalized_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_sha256
        ):
            raise UploadRejected(
                "sha256 must be a 64-character hexadecimal digest",
                UploadRejectionReason.INVALID_REQUEST,
            )

        return ValidatedUpload(
            filename=safe_name,
            media_type=expected_type,
            size_bytes=size_bytes,
            sha256=normalized_sha256,
        )

    def verify_stored(
        self,
        upload: ValidatedUpload,
        metadata: ObjectMetadata,
        prefix: bytes,
    ) -> None:
        if metadata.size_bytes != upload.size_bytes:
            raise UploadRejected(
                "stored object size does not match the upload declaration",
                UploadRejectionReason.SIZE_MISMATCH,
            )
        if metadata.content_type.lower().strip() != upload.media_type:
            raise UploadRejected(
                "上传对象的内容类型与声明不一致; 请重新选择 PDF、Markdown、JPG 或 PNG 文件。",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
        if metadata.sha256 is None or metadata.sha256.lower() != upload.sha256:
            raise UploadRejected(
                "stored object sha256 does not match the upload declaration",
                UploadRejectionReason.HASH_MISMATCH,
            )
        self._validate_content(upload.media_type, prefix)

    @classmethod
    def _validate_content(cls, media_type: str, payload: bytes) -> None:
        if media_type == MARKDOWN_MEDIA_TYPE:
            if b"\x00" in payload:
                raise UploadRejected(
                    "Markdown 不能包含二进制 NUL 字节; 请另存为 UTF-8 Markdown 后重新上传。",
                    UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
                )
            try:
                text = payload.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise UploadRejected(
                    "Markdown 必须使用 UTF-8 编码; 请转换编码后重新上传。",
                    UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
                ) from exc
            if not text.strip():
                raise UploadRejected(
                    "Markdown 文件不能为空; 请补充正文后重新上传。",
                    UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
                )
            return

        signatures = next(
            signatures
            for expected_type, signatures in cls._allowed.values()
            if expected_type == media_type and signatures is not None
        )
        if not any(payload.startswith(signature) for signature in signatures):
            raise UploadRejected(
                "文件内容与声明的类型不匹配; 请确认文件未损坏, 并转换为 PDF 或 Markdown 后重试。",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
