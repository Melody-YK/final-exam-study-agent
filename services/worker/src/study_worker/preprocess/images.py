"""Bounded image validation and normalization before optional OCR execution."""

from __future__ import annotations

import hashlib
import math
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

_SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "TIFF"})


class ImagePreprocessError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ImagePreprocessResult:
    path: Path
    sha256: str
    size_bytes: int
    width: int
    height: int
    source_format: str
    source_dpi: tuple[float, float] | None
    skew_degrees: float | None
    orientation_normalized: bool
    requires_ocr: bool = True


def preprocess_image(
    source: Path,
    destination: Path,
    *,
    max_pixels: int,
    max_input_bytes: int,
) -> ImagePreprocessResult:
    """Validate one image and write an EXIF-free RGB PNG inside the caller's sandbox."""

    if max_pixels <= 0 or max_input_bytes <= 0:
        raise ValueError("image preprocessing limits must be positive")
    if source.is_symlink() or not source.is_file():
        raise ImagePreprocessError("IMAGE_INPUT_INVALID")
    try:
        source_size = source.stat().st_size
    except OSError:
        raise ImagePreprocessError("IMAGE_INPUT_UNREADABLE", retryable=True) from None
    if source_size > max_input_bytes:
        raise ImagePreprocessError("IMAGE_INPUT_TOO_LARGE")
    if destination.is_symlink():
        raise ImagePreprocessError("IMAGE_OUTPUT_INVALID")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as probe:
                source_format = (probe.format or "").upper()
                width, height = probe.size
                probe.verify()
            with Image.open(source) as metadata:
                source_dpi = _normalized_dpi(metadata.info.get("dpi"))
                orientation = _exif_orientation(metadata)
    except Image.DecompressionBombWarning:
        raise ImagePreprocessError("PIXEL_LIMIT_EXCEEDED") from None
    except (UnidentifiedImageError, RuntimeError, SyntaxError, ValueError):
        raise ImagePreprocessError("IMAGE_DECODE_FAILED") from None
    except OSError:
        raise ImagePreprocessError("IMAGE_INPUT_UNREADABLE", retryable=True) from None

    if source_format not in _SUPPORTED_FORMATS:
        raise ImagePreprocessError("IMAGE_FORMAT_UNSUPPORTED")
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImagePreprocessError("PIXEL_LIMIT_EXCEEDED")

    try:
        with Image.open(source) as opened:
            normalized = ImageOps.exif_transpose(opened).convert("RGB")
            normalized.load()
            skew_degrees = _estimate_skew(normalized)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            normalized.save(destination, format="PNG", optimize=False, compress_level=6)
        os.chmod(destination, 0o600)
        payload = destination.read_bytes()
    except (OSError, ValueError):
        destination.unlink(missing_ok=True)
        raise ImagePreprocessError("IMAGE_PREPROCESS_FAILED", retryable=True) from None

    output_width, output_height = normalized.size
    return ImagePreprocessResult(
        path=destination,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        width=output_width,
        height=output_height,
        source_format=source_format,
        source_dpi=source_dpi,
        skew_degrees=skew_degrees,
        orientation_normalized=orientation not in {None, 1},
    )


def _normalized_dpi(value: object) -> tuple[float, float] | None:
    if isinstance(value, int | float):
        values = (float(value), float(value))
    elif isinstance(value, tuple | list) and len(value) == 2:
        try:
            values = (float(value[0]), float(value[1]))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if not all(math.isfinite(item) and item > 0 for item in values):
        return None
    return values


def _exif_orientation(image: Image.Image) -> int | None:
    try:
        value = image.getexif().get(274)
    except (AttributeError, OSError, ValueError):
        return None
    return value if isinstance(value, int) else None


def _estimate_skew(image: Image.Image) -> float | None:
    sample = image.copy()
    sample.thumbnail((256, 256))
    grayscale = sample.convert("L")
    points: list[tuple[float, float]] = []
    for y in range(grayscale.height):
        for x in range(grayscale.width):
            pixel = grayscale.getpixel((x, y))
            if isinstance(pixel, int | float) and pixel < 180:
                points.append((float(x), float(y)))
    if len(points) < 16:
        return None
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    covariance_xx = sum((point[0] - mean_x) ** 2 for point in points)
    covariance_yy = sum((point[1] - mean_y) ** 2 for point in points)
    covariance_xy = sum((point[0] - mean_x) * (point[1] - mean_y) for point in points)
    if covariance_xx == covariance_yy and covariance_xy == 0:
        return None
    angle = math.degrees(0.5 * math.atan2(2 * covariance_xy, covariance_xx - covariance_yy))
    while angle > 45:
        angle -= 90
    while angle < -45:
        angle += 90
    return round(angle, 3)
