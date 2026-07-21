from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePath
from typing import ClassVar

from study_agent.providers.protocols import ObjectMetadata


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
    _allowed: ClassVar[dict[str, tuple[str, tuple[bytes, ...]]]] = {
        ".pdf": ("application/pdf", (b"%PDF-",)),
        ".pptx": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            (b"PK\x03\x04",),
        ),
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
        self._validate_magic(upload.media_type, payload)
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
                "unsupported file extension",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
        expected_type, _ = expected
        if content_type.lower().strip() != expected_type:
            raise UploadRejected(
                "declared content type does not match extension",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
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
                "stored object content type does not match the upload declaration",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
        if metadata.sha256 is None or metadata.sha256.lower() != upload.sha256:
            raise UploadRejected(
                "stored object sha256 does not match the upload declaration",
                UploadRejectionReason.HASH_MISMATCH,
            )
        self._validate_magic(upload.media_type, prefix)

    @classmethod
    def _validate_magic(cls, media_type: str, prefix: bytes) -> None:
        signatures = next(
            signatures
            for expected_type, signatures in cls._allowed.values()
            if expected_type == media_type
        )
        if not any(prefix.startswith(signature) for signature in signatures):
            raise UploadRejected(
                "file magic does not match declared type",
                UploadRejectionReason.UNSUPPORTED_MEDIA_TYPE,
            )
