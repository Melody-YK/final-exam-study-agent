from __future__ import annotations

from pathlib import Path

import pytest

from study_worker.parsers.pdf_native import PDFNativeParser
from study_worker.parsers.pptx_native import PPTXNativeParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError, NativeParserRouter
from tests.fixtures.build_documents import build_documents, sha256

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _request(path: Path, media_type: str, output_dir: Path) -> ParseRequest:
    return ParseRequest(
        job_id="job-1",
        document_id="document-1",
        document_sha256=sha256(path),
        media_type=media_type,
        input_path=path,
        output_dir=output_dir,
    )


@pytest.mark.asyncio
async def test_native_router_uses_exact_media_type_allowlist(tmp_path: Path) -> None:
    fixtures = build_documents(tmp_path / "fixtures")
    router = NativeParserRouter(
        (
            PDFNativeParser(max_pages=20, max_pixels=10_000_000),
            PPTXNativeParser(max_pages=20, max_pixels=10_000_000),
        )
    )

    pdf = await router.parse(_request(fixtures.pdf, "application/pdf", tmp_path / "pdf"))
    pptx = await router.parse(_request(fixtures.pptx, PPTX_MEDIA_TYPE, tmp_path / "pptx"))

    assert pdf.source_backend == "pdf-native"
    assert pptx.source_backend == "pptx-native"
    assert router.capability.profile == "native-v1"
    assert router.capability.supports_ocr is False

    with pytest.raises(NativeParserError, match="UNSUPPORTED_MEDIA_TYPE"):
        await router.parse(_request(fixtures.pdf, "image/png", tmp_path / "image"))
