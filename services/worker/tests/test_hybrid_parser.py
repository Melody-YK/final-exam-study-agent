from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from study_contracts import BlockType
from study_worker.parsers.hybrid import HybridPdfParser
from study_worker.parsers.normalize import RawBlock, RawBoundingBox, RawDocument, RawPage
from study_worker.parsers.protocols import ParseRequest, ParserExecutionError
from study_worker.sandbox import Sandbox


def _document(
    backend: str,
    *,
    structural_text: str | None = None,
    text: str = "A sufficiently detailed native paragraph for the quality gate.",
) -> RawDocument:
    blocks = [
        RawBlock(
            type=BlockType.PARAGRAPH,
            text=text,
            bbox=RawBoundingBox(x0=10, top=10, x1=500, bottom=100),
            reading_order=0,
        )
    ]
    if structural_text is not None:
        blocks.append(
            RawBlock(
                type=BlockType.IMAGE,
                text=structural_text,
                bbox=RawBoundingBox(x0=20, top=120, x1=480, bottom=500),
                reading_order=1,
            )
        )
    return RawDocument(
        document_sha256="a" * 64,
        source_backend=backend,
        source_version="test-1",
        total_page_count=1,
        pages=[
            RawPage(
                ordinal=1,
                width=600,
                height=800,
                source_kind="page",
                native_text_present=bool(text or structural_text),
                blocks=blocks,
            )
        ],
    )


@dataclass
class StubParser:
    result: RawDocument | ParserExecutionError
    calls: list[int] = field(default_factory=list)

    async def parse(
        self,
        request: ParseRequest,
        *,
        sandbox: Sandbox,
        timeout_seconds: float,
    ) -> RawDocument:
        del sandbox, timeout_seconds
        self.calls.append(request.requested_pages[0])
        if isinstance(self.result, ParserExecutionError):
            raise self.result
        return self.result


def _request(tmp_path: Path) -> tuple[ParseRequest, Sandbox]:
    sandbox = Sandbox(tmp_path, tmp_path / "input.bin", tmp_path / "output")
    return (
        ParseRequest(
            job_id="job-1",
            document_id="document-1",
            document_sha256="a" * 64,
            media_type="application/pdf",
            input_path=sandbox.input_path,
            output_dir=sandbox.output_dir,
            requested_pages=(1,),
        ),
        sandbox,
    )


@pytest.mark.asyncio
async def test_hybrid_accepts_a_good_fast_page_without_calling_docling(tmp_path: Path) -> None:
    native = StubParser(_document("pdf-native"))
    standard = StubParser(_document("docling-standard"))
    parser = HybridPdfParser(native=native, docling_standard=standard)
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "pdf-native"
    assert native.calls == [1]
    assert standard.calls == []
    assert result.pages[0].metadata["parser_route"] == "fast"
    assert result.pages[0].metadata["parser_route_reason"] == "FAST_PARSE_ACCEPTED"


@pytest.mark.asyncio
async def test_hybrid_uses_standard_docling_for_unresolved_structure(tmp_path: Path) -> None:
    native = StubParser(_document("pdf-native", structural_text=""))
    standard = StubParser(
        _document("docling-standard", structural_text="A diagram of the parsing pipeline")
    )
    vlm = StubParser(_document("docling-vlm", structural_text="VLM description"))
    parser = HybridPdfParser(
        native=native,
        docling_standard=standard,
        docling_vlm=vlm,
    )
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "docling-standard"
    assert native.calls == [1]
    assert standard.calls == [1]
    assert vlm.calls == []
    assert result.pages[0].metadata["parser_route_reason"] == "UNRESOLVED_STRUCTURAL_CONTENT"


@pytest.mark.asyncio
async def test_hybrid_uses_vlm_only_when_standard_still_has_a_gap(tmp_path: Path) -> None:
    native = StubParser(_document("pdf-native", structural_text=""))
    standard = StubParser(_document("docling-standard", structural_text=""))
    vlm = StubParser(_document("docling-vlm", structural_text="Resolved visual explanation"))
    parser = HybridPdfParser(
        native=native,
        docling_standard=standard,
        docling_vlm=vlm,
    )
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "docling-vlm"
    assert native.calls == standard.calls == vlm.calls == [1]
    assert result.pages[0].metadata["parser_route"] == "docling-vlm"
    assert result.pages[0].metadata["parser_route_reason"] == "STRUCTURE_REQUIRES_VLM"


@pytest.mark.asyncio
async def test_hybrid_can_recover_when_the_fast_parser_rejects_the_page(tmp_path: Path) -> None:
    native = StubParser(ParserExecutionError("PDF_PARSE_FAILED", retryable=True))
    standard = StubParser(_document("docling-standard"))
    parser = HybridPdfParser(native=native, docling_standard=standard)
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "docling-standard"
    assert result.pages[0].metadata["parser_route_reason"] == "FAST_PARSE_FAILED"


@pytest.mark.asyncio
async def test_hybrid_tries_vlm_when_standard_fails_after_a_weak_fast_result(
    tmp_path: Path,
) -> None:
    native = StubParser(_document("pdf-native", structural_text=""))
    standard = StubParser(ParserExecutionError("DOCLING_PARSE_FAILED", retryable=True))
    vlm = StubParser(_document("docling-vlm", structural_text="Resolved visual explanation"))
    parser = HybridPdfParser(
        native=native,
        docling_standard=standard,
        docling_vlm=vlm,
    )
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "docling-vlm"
    assert native.calls == standard.calls == vlm.calls == [1]
    assert result.pages[0].metadata["parser_candidates_tried"] == 3


@pytest.mark.asyncio
async def test_hybrid_tries_vlm_when_fast_and_standard_both_fail(tmp_path: Path) -> None:
    native = StubParser(ParserExecutionError("PDF_PARSE_FAILED", retryable=True))
    standard = StubParser(ParserExecutionError("DOCLING_PARSE_FAILED", retryable=False))
    vlm = StubParser(_document("docling-vlm", structural_text="Recovered page"))
    parser = HybridPdfParser(
        native=native,
        docling_standard=standard,
        docling_vlm=vlm,
    )
    request, sandbox = _request(tmp_path)

    result = await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert result.source_backend == "docling-vlm"
    assert native.calls == standard.calls == vlm.calls == [1]


@pytest.mark.asyncio
async def test_hybrid_reports_one_failure_after_all_backends_fail(tmp_path: Path) -> None:
    native = StubParser(ParserExecutionError("PDF_PARSE_FAILED", retryable=True))
    standard = StubParser(ParserExecutionError("DOCLING_PARSE_FAILED"))
    vlm = StubParser(ParserExecutionError("DOCLING_PARSE_FAILED"))
    parser = HybridPdfParser(
        native=native,
        docling_standard=standard,
        docling_vlm=vlm,
    )
    request, sandbox = _request(tmp_path)

    with pytest.raises(ParserExecutionError) as failure:
        await parser.parse(request, sandbox=sandbox, timeout_seconds=1)

    assert failure.value.code == "ENHANCED_PARSE_FAILED"
    assert failure.value.retryable is True
    assert native.calls == standard.calls == vlm.calls == [1]
