from __future__ import annotations

import hashlib
import socket
import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pypdf import PdfReader

from study_worker.parsers.ooxml import (
    OOXMLSecurityError,
    OOXMLSecurityPolicy,
    inspect_ooxml,
)
from tests.fixtures.build_documents import build_documents, sha256


def test_self_authored_fixtures_are_deterministic_and_readable(tmp_path: Path) -> None:
    first = build_documents(tmp_path / "first")
    second = build_documents(tmp_path / "second")

    assert sha256(first.pdf) == sha256(second.pdf)
    assert sha256(first.pptx) == sha256(second.pptx)
    assert len(PdfReader(first.pdf).pages) == 2
    assert len(Presentation(first.pptx).slides) == 1


def test_ooxml_preflight_reports_metadata_without_following_external_relationships(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = build_documents(tmp_path).pptx

    def fail_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("OOXML inspection must not use the network")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    inspection = inspect_ooxml(fixture, OOXMLSecurityPolicy())

    assert inspection.has_ole is True
    assert inspection.has_smartart is True
    assert inspection.has_omml is True
    assert len(inspection.external_relationships) == 1
    assert inspection.external_relationships[0].target_scheme == "https"
    assert "example.invalid" not in repr(inspection.external_relationships[0])


def test_ooxml_preflight_rejects_macro_parts(tmp_path: Path) -> None:
    fixture = build_documents(tmp_path).macro_pptx

    with pytest.raises(OOXMLSecurityError, match="MACRO_CONTENT_BLOCKED"):
        inspect_ooxml(fixture, OOXMLSecurityPolicy())


def test_ooxml_preflight_rejects_traversal_and_duplicate_entries(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.pptx"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
        archive.writestr("../outside.bin", b"blocked")

    with pytest.raises(OOXMLSecurityError, match="OOXML_PATH_INVALID"):
        inspect_ooxml(traversal, OOXMLSecurityPolicy())

    duplicate = tmp_path / "duplicate.pptx"
    with (
        pytest.warns(UserWarning, match="Duplicate name"),
        zipfile.ZipFile(duplicate, "w") as archive,
    ):
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
        archive.writestr("ppt/presentation.xml", b"<p:other/>")

    with pytest.raises(OOXMLSecurityError, match="OOXML_DUPLICATE_ENTRY"):
        inspect_ooxml(duplicate, OOXMLSecurityPolicy())


def test_ooxml_preflight_rejects_resource_amplification(tmp_path: Path) -> None:
    compressed = tmp_path / "compressed.pptx"
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/presentation.xml", b"<p:presentation/>")
        archive.writestr("ppt/slides/slide1.xml", b"A" * 100_000)

    policy = OOXMLSecurityPolicy(max_compression_ratio=10)
    with pytest.raises(OOXMLSecurityError, match="OOXML_COMPRESSION_RATIO_EXCEEDED"):
        inspect_ooxml(compressed, policy)


def test_fixture_builder_does_not_embed_machine_paths(tmp_path: Path) -> None:
    fixtures = build_documents(tmp_path / "fixtures")
    machine_path = str(Path.home()).encode()

    assert machine_path not in fixtures.pdf.read_bytes()
    assert machine_path not in fixtures.pptx.read_bytes()
    assert hashlib.sha256(fixtures.pdf.read_bytes()).hexdigest() == sha256(fixtures.pdf)
