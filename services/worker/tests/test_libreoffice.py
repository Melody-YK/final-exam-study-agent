from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from study_worker.rendering.libreoffice import (
    LibreOfficeRenderer,
    RendererError,
    RendererStatus,
    classify_libreoffice_version,
)
from study_worker.sandbox import SandboxManager


def test_libreoffice_version_classification_is_fail_closed() -> None:
    assert classify_libreoffice_version("LibreOffice 24.2.1.2") is RendererStatus.STABLE
    assert (
        classify_libreoffice_version("LibreOfficeDev 26.8.0.0.alpha0 build")
        is RendererStatus.UNSTABLE
    )
    assert classify_libreoffice_version("LibreOffice 25.0 beta1") is RendererStatus.UNSTABLE
    assert classify_libreoffice_version("unexpected output") is RendererStatus.UNSTABLE


@pytest.mark.asyncio
async def test_missing_or_unstable_libreoffice_never_renders(tmp_path: Path) -> None:
    missing = LibreOfficeRenderer(tmp_path / "missing-soffice")
    manager = SandboxManager(tmp_path / "worker")
    with manager.create() as sandbox:
        probe = await missing.probe(sandbox)
        assert probe.status is RendererStatus.UNAVAILABLE
        with pytest.raises(RendererError, match="RENDERER_UNAVAILABLE"):
            await missing.render(sandbox, timeout_seconds=1)

    unstable_executable = _fake_soffice(
        tmp_path / "soffice-dev",
        version="LibreOfficeDev 26.8.0.0.alpha0",
    )
    unstable = LibreOfficeRenderer(unstable_executable)
    with manager.create() as sandbox:
        assert (await unstable.probe(sandbox)).status is RendererStatus.UNSTABLE
        with pytest.raises(RendererError, match="RENDERER_UNSTABLE"):
            await unstable.render(sandbox, timeout_seconds=1)


@pytest.mark.asyncio
async def test_stable_libreoffice_uses_fixed_argv_and_hashes_render(tmp_path: Path) -> None:
    executable = _fake_soffice(tmp_path / "soffice", version="LibreOffice 24.2.1.2")
    renderer = LibreOfficeRenderer(executable)
    manager = SandboxManager(tmp_path / "worker")

    with manager.create() as sandbox:
        sandbox.input_path.write_bytes(b"self-authored pptx placeholder")
        result = await renderer.render(sandbox, timeout_seconds=2)

        assert result.path.is_file()
        assert result.path.read_bytes() == b"%PDF-self-authored-render"
        assert result.size_bytes == len(b"%PDF-self-authored-render")
        assert len(result.sha256) == 64
        assert result.version == "LibreOffice 24.2.1.2"


def _fake_soffice(path: Path, *, version: str) -> Path:
    script = f"""#!{sys.executable}
import pathlib
import sys

if "--version" in sys.argv:
    print({version!r})
    raise SystemExit(0)
outdir = pathlib.Path(sys.argv[sys.argv.index("--outdir") + 1])
outdir.mkdir(parents=True, exist_ok=True)
(outdir / "render-input.pdf").write_bytes(b"%PDF-self-authored-render")
"""
    path.write_text(script, encoding="utf-8")
    os.chmod(path, 0o700)
    return path.resolve()
