from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from study_worker.parsers.native_process import NativeSubprocessParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.parsers.router import NativeParserError
from study_worker.sandbox import SandboxManager
from tests.fixtures.build_documents import build_documents, sha256


@pytest.mark.asyncio
async def test_native_parser_runs_in_restricted_child_and_returns_raw_document(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    manager = SandboxManager(tmp_path / "worker")
    parser = NativeSubprocessParser(
        max_pages=20,
        max_pixels=10_000_000,
        max_result_bytes=10_000_000,
    )

    with manager.create() as sandbox:
        shutil.copyfile(fixture, sandbox.input_path)
        result = await parser.parse(
            ParseRequest(
                job_id="job-1",
                document_id="document-1",
                document_sha256=sha256(fixture),
                media_type="application/pdf",
                input_path=sandbox.input_path,
                output_dir=sandbox.output_dir,
                requested_pages=(2,),
            ),
            sandbox=sandbox,
            timeout_seconds=5,
        )

        assert result.total_page_count == 2
        assert [page.ordinal for page in result.pages] == [2]
        assert (sandbox.output_dir / "raw-document.json").is_file()


@pytest.mark.asyncio
async def test_native_parser_child_returns_stable_macro_error_without_traceback(
    tmp_path: Path,
) -> None:
    fixture = build_documents(tmp_path / "fixtures").macro_pptx
    manager = SandboxManager(tmp_path / "worker")
    parser = NativeSubprocessParser(
        max_pages=20,
        max_pixels=10_000_000,
        max_result_bytes=10_000_000,
    )

    with manager.create() as sandbox:
        shutil.copyfile(fixture, sandbox.input_path)
        with pytest.raises(NativeParserError, match="MACRO_CONTENT_BLOCKED") as caught:
            await parser.parse(
                ParseRequest(
                    job_id="job-1",
                    document_id="document-1",
                    document_sha256=sha256(fixture),
                    media_type=(
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    ),
                    input_path=sandbox.input_path,
                    output_dir=sandbox.output_dir,
                ),
                sandbox=sandbox,
                timeout_seconds=5,
            )

    assert "Traceback" not in str(caught.value)
    assert str(fixture) not in str(caught.value)
