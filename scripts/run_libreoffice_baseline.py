"""Run text-free stable LibreOffice rendering observations.

The report contains only hashes, sizes, timings, resource metadata, and the
renderer version. Private source paths and document content never leave the
local ignored evidence directory.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from study_worker.rendering.libreoffice import (  # noqa: E402
    LibreOfficeRenderer,
    RendererStatus,
)
from study_worker.sandbox import SandboxManager  # noqa: E402
from tests.fixtures.build_documents import build_documents  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _page_hashes(pdf_path: Path, output_root: Path) -> list[str]:
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise RuntimeError("pdftoppm is required for visual baseline hashing")
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    prefix = output_root / "page"
    subprocess.run(
        [pdftoppm, "-png", "-r", "120", str(pdf_path), str(prefix)],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )
    pages = sorted(output_root.glob("page-*.png"))
    if not pages:
        raise RuntimeError("LibreOffice output produced no renderable pages")
    return [_sha256(page) for page in pages]


async def _render_once(
    renderer: LibreOfficeRenderer,
    manager: SandboxManager,
    source: Path,
    *,
    label: str,
) -> dict[str, object]:
    with manager.create() as sandbox:
        sandbox.input_path.write_bytes(source.read_bytes())
        before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        started = time.perf_counter()
        result = await renderer.render(sandbox, timeout_seconds=120)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        page_hashes = _page_hashes(result.path, sandbox.root / "visual-pages")
        return {
            "label": label,
            "input_sha256": _sha256(source),
            "pdf_sha256": result.sha256,
            "pdf_size_bytes": result.size_bytes,
            "page_count": len(page_hashes),
            "page_pixel_hashes": page_hashes,
            "duration_ms": duration_ms,
            "peak_child_rss_bytes": max(0, after - before),
            "version": result.version,
        }


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    output = arguments.output.expanduser().absolute()
    if output.is_symlink():
        raise ValueError("output must not be a symlink")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    work_root = output.parent / "libreoffice-work"
    manager = SandboxManager(work_root)
    renderer = LibreOfficeRenderer(arguments.soffice.expanduser().resolve(strict=True))
    with manager.create() as sandbox:
        probe = await renderer.probe(sandbox)
    if probe.status is not RendererStatus.STABLE or probe.version is None:
        raise RuntimeError(f"stable LibreOffice unavailable: {probe.status.value}")

    fixture_root = output.parent / "libreoffice-fixture"
    fixture = build_documents(fixture_root).pptx
    runs = [
        await _render_once(renderer, manager, fixture, label=f"self-authored-{index}")
        for index in range(1, arguments.repeat + 1)
    ]
    if arguments.private_source is not None:
        private_source = arguments.private_source.expanduser().resolve(strict=True)
        if private_source.is_symlink() or not private_source.is_file():
            raise ValueError("private source must be a regular non-symlink file")
        runs.append(
            await _render_once(
                renderer,
                manager,
                private_source,
                label="private-authorized-smoke",
            )
        )

    public_visuals: set[tuple[str, ...]] = set()
    for run in runs:
        if not str(run["label"]).startswith("self-authored"):
            continue
        hashes = run["page_pixel_hashes"]
        if not isinstance(hashes, list) or any(not isinstance(item, str) for item in hashes):
            raise RuntimeError("visual page hashes are invalid")
        public_visuals.add(tuple(hashes))
    return {
        "schema_version": "1.0",
        "renderer_status": probe.status.value,
        "renderer_version": probe.version,
        "self_authored_repeat_count": arguments.repeat,
        "self_authored_pixel_reproducible": len(public_visuals) == 1,
        "private_authorized_smoke_executed": any(
            run["label"] == "private-authorized-smoke" for run in runs
        ),
        "contains_raw_text": False,
        "contains_source_paths": False,
        "production_readiness": "not-assessed",
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--soffice", required=True, type=Path)
    parser.add_argument("--private-source", type=Path)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local/evidence/libreoffice-visual-baseline.json"),
    )
    arguments = parser.parse_args()
    if arguments.repeat < 2:
        raise ValueError("repeat must be at least 2")
    report = asyncio.run(_run(arguments))
    target = arguments.output.expanduser().absolute()
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    os.chmod(target, 0o600)
    print(f"renderer_status={report['renderer_status']}")
    print(
        "self_authored_pixel_reproducible="
        + str(report["self_authored_pixel_reproducible"]).lower()
    )
    print(
        "private_authorized_smoke_executed="
        + str(report["private_authorized_smoke_executed"]).lower()
    )
    return 0 if report["self_authored_pixel_reproducible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
