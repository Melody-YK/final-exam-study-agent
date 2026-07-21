from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from study_worker.preprocess.images import ImagePreprocessError, preprocess_image


def test_preprocess_normalizes_orientation_strips_metadata_and_estimates_skew(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.jpg"
    image = Image.new("RGB", (120, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.line((15, 25, 105, 35), fill="black", width=4)
    exif = image.getexif()
    exif[274] = 6
    exif[270] = "private metadata"
    image.save(source, exif=exif, dpi=(144, 144))

    output = tmp_path / "sandbox" / "normalized.png"
    result = preprocess_image(
        source,
        output,
        max_pixels=1_000_000,
        max_input_bytes=1_000_000,
    )

    assert result.path == output
    assert (result.width, result.height) == (60, 120)
    assert result.orientation_normalized is True
    assert result.source_format == "JPEG"
    assert result.source_dpi == pytest.approx((144, 144), rel=0.02)
    assert result.skew_degrees is not None
    assert result.requires_ocr is True
    assert len(result.sha256) == 64
    assert os.stat(output).st_mode & 0o777 == 0o600
    with Image.open(output) as normalized:
        assert normalized.format == "PNG"
        assert normalized.mode == "RGB"
        assert 270 not in normalized.getexif()


def test_preprocess_rejects_bad_image_pixel_limit_size_and_symlink(tmp_path: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not-an-image")
    with pytest.raises(ImagePreprocessError, match="IMAGE_DECODE_FAILED"):
        preprocess_image(bad, tmp_path / "bad-out.png", max_pixels=10, max_input_bytes=100)

    image_path = tmp_path / "large.png"
    Image.new("RGB", (11, 10), "white").save(image_path)
    with pytest.raises(ImagePreprocessError, match="PIXEL_LIMIT_EXCEEDED"):
        preprocess_image(
            image_path,
            tmp_path / "large-out.png",
            max_pixels=100,
            max_input_bytes=10_000,
        )
    with pytest.raises(ImagePreprocessError, match="IMAGE_INPUT_TOO_LARGE"):
        preprocess_image(
            image_path,
            tmp_path / "size-out.png",
            max_pixels=1_000,
            max_input_bytes=1,
        )

    symlink = tmp_path / "link.png"
    symlink.symlink_to(image_path)
    with pytest.raises(ImagePreprocessError, match="IMAGE_INPUT_INVALID"):
        preprocess_image(
            symlink,
            tmp_path / "link-out.png",
            max_pixels=1_000,
            max_input_bytes=10_000,
        )
