from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from study_worker.parsers import subprocess_entry
from study_worker.parsers.router import NativeParserError
from tests.fixtures.build_documents import build_documents


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", ()), ("1", (1,)), ("2,1", (2, 1))],
)
def test_requested_page_parser_accepts_only_unique_positive_ordinals(
    value: str, expected: tuple[int, ...]
) -> None:
    assert subprocess_entry._parse_requested_pages(value) == expected


@pytest.mark.parametrize("value", ["a", "0", "-1", "1,1"])
def test_requested_page_parser_rejects_invalid_values(value: str) -> None:
    with pytest.raises(NativeParserError, match="REQUESTED_PAGE_INVALID"):
        subprocess_entry._parse_requested_pages(value)


def test_child_sandbox_path_rejects_escape_symlink_and_missing_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "input.pdf"
    source.write_bytes(b"fixture")
    assert subprocess_entry._sandbox_path("input.pdf", must_exist=True) == source
    assert subprocess_entry._sandbox_path("output/result.json", must_exist=False) == (
        tmp_path / "output/result.json"
    )

    linked = tmp_path / "linked.pdf"
    linked.symlink_to(source)
    for candidate in (
        str(source),
        "../outside",
        "folder\\file",
        "nul\x00file",
        "linked.pdf",
    ):
        with pytest.raises(NativeParserError, match="PARSER_CHILD_PATH_INVALID"):
            subprocess_entry._sandbox_path(candidate, must_exist=True)
    with pytest.raises(NativeParserError, match="PARSER_CHILD_INPUT_MISSING"):
        subprocess_entry._sandbox_path("missing.pdf", must_exist=True)


@pytest.mark.asyncio
async def test_child_run_parses_one_requested_pdf_page_and_writes_private_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture = build_documents(tmp_path / "fixtures").pdf
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    source = sandbox / "input.pdf"
    shutil.copyfile(fixture, source)
    monkeypatch.chdir(sandbox)
    arguments = argparse.Namespace(
        media_type="application/pdf",
        document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        requested_pages="2",
        max_pages=20,
        max_pixels=10_000_000,
        input="input.pdf",
        output="output/raw-document.json",
    )

    assert await subprocess_entry._run(arguments) == 0

    output = sandbox / "output/raw-document.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_page_count"] == 2
    assert [page["ordinal"] for page in payload["pages"]] == [2]
    assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", [NativeParserError("PDF_INVALID"), RuntimeError("boom")])
def test_child_main_returns_sanitized_error_envelopes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: Exception
) -> None:
    async def fail(_arguments: argparse.Namespace) -> int:
        raise failure

    monkeypatch.setattr(subprocess_entry, "_run", fail)
    monkeypatch.setattr(
        subprocess_entry,
        "_parser",
        lambda: type("Parser", (), {"parse_args": lambda self: argparse.Namespace()})(),
    )

    exit_code = subprocess_entry.main()
    payload = json.loads(capsys.readouterr().out)

    assert exit_code in {2, 3}
    assert payload["code"] in {"PDF_INVALID", "PARSER_CHILD_FAILED"}
    assert "boom" not in json.dumps(payload)
