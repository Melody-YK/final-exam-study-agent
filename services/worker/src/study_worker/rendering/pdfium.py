"""Bounded PDF page rendering for persisted visual evidence assets."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image

PDF_RENDER_SCALE = 2.0
PNG_MEDIA_TYPE = "image/png"


class PdfRenderError(RuntimeError):
    """A sanitized failure while turning one PDF page into a PNG."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PdfRenderResult:
    path: Path
    sha256: str
    size_bytes: int
    width: int
    height: int
    scale: float


def render_pdf_page(
    input_path: Path,
    destination: Path,
    *,
    ordinal: int,
    expected_sha256: str,
    max_pages: int,
    max_pixels: int,
) -> PdfRenderResult:
    """Render exactly one page into an atomically-created private PNG."""

    if ordinal < 1:
        raise PdfRenderError("PDF_RENDER_PAGE_OUT_OF_RANGE")
    if max_pages <= 0 or max_pixels <= 0:
        raise ValueError("PDF render limits must be positive")
    try:
        if input_path.is_symlink() or not input_path.is_file():
            raise OSError
        digest = _sha256(input_path)
    except OSError:
        raise PdfRenderError("PDF_RENDER_INPUT_UNREADABLE", retryable=True) from None
    if digest != expected_sha256:
        raise PdfRenderError("PDF_RENDER_INPUT_HASH_MISMATCH")

    try:
        document = pdfium.PdfDocument(input_path)
    except Exception:
        raise PdfRenderError("PDF_RENDER_INVALID") from None

    try:
        page_count = len(document)
        if page_count <= 0 or page_count > max_pages:
            raise PdfRenderError("PDF_RENDER_PAGE_COUNT_INVALID")
        if ordinal > page_count:
            raise PdfRenderError("PDF_RENDER_PAGE_OUT_OF_RANGE")
        page = document[ordinal - 1]
        try:
            width_points, height_points = page.get_size()
            if not all(
                math.isfinite(value) and value > 0
                for value in (width_points, height_points)
            ):
                raise PdfRenderError("PDF_RENDER_INVALID")
            width = math.ceil(width_points * PDF_RENDER_SCALE)
            height = math.ceil(height_points * PDF_RENDER_SCALE)
            if width <= 0 or height <= 0:
                raise PdfRenderError("PDF_RENDER_INVALID")
            if width * height > max_pixels:
                raise PdfRenderError("PDF_RENDER_PIXEL_LIMIT_EXCEEDED")
            bitmap = page.render(scale=PDF_RENDER_SCALE)
            try:
                if (bitmap.width, bitmap.height) != (width, height):
                    raise PdfRenderError("PDF_RENDER_INVALID")
                if bitmap.width * bitmap.height > max_pixels:
                    raise PdfRenderError("PDF_RENDER_PIXEL_LIMIT_EXCEEDED")
                image = bitmap.to_pil().convert("RGB")
                try:
                    if image.size != (width, height):
                        raise PdfRenderError("PDF_RENDER_INVALID")
                    if image.width * image.height > max_pixels:
                        raise PdfRenderError("PDF_RENDER_PIXEL_LIMIT_EXCEEDED")
                    _write_png(image, destination)
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    except PdfRenderError:
        raise
    except OSError:
        raise PdfRenderError("PDF_RENDER_OUTPUT_FAILED", retryable=True) from None
    except Exception:
        raise PdfRenderError("PDF_RENDER_FAILED", retryable=True) from None
    finally:
        document.close()

    try:
        size_bytes = destination.stat().st_size
        sha256 = _sha256(destination)
    except OSError:
        raise PdfRenderError("PDF_RENDER_OUTPUT_FAILED", retryable=True) from None
    return PdfRenderResult(
        path=destination,
        sha256=sha256,
        size_bytes=size_bytes,
        width=width,
        height=height,
        scale=PDF_RENDER_SCALE,
    )


def _write_png(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise OSError("PDF render output directory is invalid")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            image.save(stream, format="PNG", optimize=False, compress_level=6)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        os.chmod(destination, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
