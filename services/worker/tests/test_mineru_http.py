from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pypdf import PdfWriter

from study_contracts import BlockType
from study_worker.capabilities import probe_mineru_api
from study_worker.parsers.mineru_http import MineruHttpParser
from study_worker.parsers.protocols import ParseRequest
from study_worker.sandbox import Sandbox


def _pdf(path: Path, *, pages: int = 2) -> str:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=600, height=800)
    with path.open("wb") as stream:
        writer.write(stream)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(sandbox: Sandbox, digest: str, *, pages: tuple[int, ...] = ()) -> ParseRequest:
    return ParseRequest(
        job_id="job-mineru",
        document_id="document-1",
        document_sha256=digest,
        media_type="application/pdf",
        input_path=sandbox.input_path,
        output_dir=sandbox.output_dir,
        requested_pages=pages,
    )


@pytest.mark.asyncio
async def test_mineru_health_probe_is_fail_closed_and_preserves_version() -> None:
    def healthy(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["Authorization"] == "Bearer local-token"
        return httpx.Response(
            200,
            json={"status": "healthy", "version": "3.4.4", "protocol_version": 2},
        )

    status = await probe_mineru_api(
        base_url="http://mineru.local",
        token=SecretStr("local-token"),
        transport=httpx.MockTransport(healthy),
    )
    invalid = await probe_mineru_api(
        base_url="http://mineru.local",
        token=None,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="not-json")),
    )

    assert status.ready is True
    assert status.source_version == "3.4.4"
    assert status.protocol_version == 2
    assert invalid.ready is False
    assert invalid.reason_code == "MINERU_HEALTH_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_mineru_maps_one_response_to_ordered_raw_pages(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, tmp_path / "input.bin", tmp_path / "output")
    sandbox.output_dir.mkdir()
    digest = _pdf(sandbox.input_path)
    content = [
        {
            "type": "text",
            "text": "Compiler construction",
            "text_level": 1,
            "bbox": [50, 40, 900, 120],
            "page_idx": 0,
        },
        {
            "type": "table",
            "table_body": "| token | kind |\n|---|---|",
            "bbox": [100, 200, 900, 800],
            "page_idx": 1,
        },
    ]

    def parse(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/file_parse"
        assert b'name="backend"' in request.content
        assert b"pipeline" in request.content
        assert b'name="files"' in request.content
        return httpx.Response(
            200,
            json={"results": {"input": {"content_list": json.dumps(content)}}},
        )

    parser = MineruHttpParser(
        base_url="http://mineru.local",
        token=None,
        source_version="3.4.4",
        backend="pipeline",
        max_pages=10,
        max_result_bytes=1_000_000,
        transport=httpx.MockTransport(parse),
    )

    result = await parser.parse(
        _request(sandbox, digest),
        sandbox=sandbox,
        timeout_seconds=2,
    )

    assert result.parser_profile == "mineru-v1"
    assert result.source_backend == "mineru-pipeline"
    assert [page.ordinal for page in result.pages] == [1, 2]
    assert result.pages[0].blocks[0].type is BlockType.TITLE
    assert result.pages[1].blocks[0].type is BlockType.TABLE
    assert result.pages[1].blocks[0].text.startswith("| token")
    assert result.pages[0].blocks[0].bbox.x1 == pytest.approx(540)


@pytest.mark.asyncio
async def test_mineru_offsets_relative_page_indexes_for_partial_retries(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, tmp_path / "input.bin", tmp_path / "output")
    sandbox.output_dir.mkdir()
    digest = _pdf(sandbox.input_path, pages=3)
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={
                "results": {
                    "input": {
                        "content_list": json.dumps(
                            [
                                {
                                    "type": "text",
                                    "text": "Only the selected page",
                                    "bbox": [0, 0, 1000, 1000],
                                    "page_idx": 0,
                                }
                            ]
                        )
                    }
                }
            },
        )
    )
    parser = MineruHttpParser(
        base_url="http://mineru.local",
        token=None,
        source_version="3.4.4",
        backend="pipeline",
        max_pages=10,
        max_result_bytes=1_000_000,
        transport=transport,
    )

    result = await parser.parse(
        _request(sandbox, digest, pages=(3,)),
        sandbox=sandbox,
        timeout_seconds=2,
    )

    assert [page.ordinal for page in result.pages] == [3]
    assert result.pages[0].blocks[0].text == "Only the selected page"


@pytest.mark.asyncio
async def test_mineru_maps_overlapping_relative_indexes_for_a_partial_range(
    tmp_path: Path,
) -> None:
    sandbox = Sandbox(tmp_path, tmp_path / "input.bin", tmp_path / "output")
    sandbox.output_dir.mkdir()
    digest = _pdf(sandbox.input_path, pages=4)

    def parse(request: httpx.Request) -> httpx.Response:
        assert b'name="start_page_id"' in request.content
        assert b"\r\n1\r\n" in request.content
        assert b'name="end_page_id"' in request.content
        assert b"\r\n2\r\n" in request.content
        content = [
            {
                "type": "text",
                "text": "Original page two",
                "bbox": [0, 0, 1000, 1000],
                "page_idx": 0,
            },
            {
                "type": "text",
                "text": "Original page three",
                "bbox": [0, 0, 1000, 1000],
                "page_idx": 1,
            },
        ]
        return httpx.Response(
            200,
            json={"results": {"input": {"content_list": json.dumps(content)}}},
        )

    parser = MineruHttpParser(
        base_url="http://mineru.local",
        token=None,
        source_version="3.4.4",
        backend="pipeline",
        max_pages=10,
        max_result_bytes=1_000_000,
        transport=httpx.MockTransport(parse),
    )

    result = await parser.parse(
        _request(sandbox, digest, pages=(2, 3)),
        sandbox=sandbox,
        timeout_seconds=2,
    )

    assert [page.ordinal for page in result.pages] == [2, 3]
    assert [page.blocks[0].text for page in result.pages] == [
        "Original page two",
        "Original page three",
    ]


@pytest.mark.asyncio
async def test_mineru_rejects_page_indexes_outside_the_rewritten_range(tmp_path: Path) -> None:
    sandbox = Sandbox(tmp_path, tmp_path / "input.bin", tmp_path / "output")
    sandbox.output_dir.mkdir()
    digest = _pdf(sandbox.input_path, pages=3)
    content = [{"type": "text", "text": "wrong page", "page_idx": 1}]
    parser = MineruHttpParser(
        base_url="http://mineru.local",
        token=None,
        source_version="3.4.4",
        backend="pipeline",
        max_pages=10,
        max_result_bytes=1_000_000,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"results": {"input": {"content_list": json.dumps(content)}}},
            )
        ),
    )

    with pytest.raises(Exception, match="MINERU_RESULT_INVALID"):
        await parser.parse(
            _request(sandbox, digest, pages=(3,)),
            sandbox=sandbox,
            timeout_seconds=2,
        )
