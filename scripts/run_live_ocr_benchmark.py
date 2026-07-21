"""Run a local-only PaddleOCR benchmark through the isolated profile executable."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.ocr.manifest import OcrEvalEntry, OcrEvalManifest  # noqa: E402
from evals.ocr.run_benchmark import (  # noqa: E402
    ExternalObservation,
    run_benchmark,
    write_report,
)
from study_contracts import Page  # noqa: E402
from study_worker.parsers.paddle_general import (  # noqa: E402
    normalize_paddle_general_output,
)

_DEFAULT_PROFILE = (
    _ROOT
    / "services"
    / "worker"
    / "profiles"
    / "paddle"
    / ".venv"
    / "bin"
    / "study-agent-paddle-profile"
)
_DEFAULT_CACHE = _ROOT / ".local" / "models" / "paddlex"
_DEFAULT_OUTPUT_ROOT = _ROOT / ".local" / "evals" / "ocr"
_IMAGE_EXTENSIONS = frozenset({".jpeg", ".jpg", ".png", ".tif", ".tiff"})


def _private_directory(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise ValueError("private source root must be an absolute non-symlink directory")
    resolved = expanded.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("private source root must be a directory")
    return resolved


def _private_file(path: Path, *, executable: bool = False) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise ValueError("configured file must be an absolute non-symlink file")
    resolved = expanded.resolve(strict=True)
    if not resolved.is_file() or (executable and not os.access(resolved, os.X_OK)):
        raise ValueError("configured file is unavailable")
    return resolved


def _private_cache(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise ValueError("model cache must be an absolute non-symlink directory")
    resolved = expanded.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("model cache is unavailable")
    return resolved


def _write_private_json(path: Path, payload: object) -> Path:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("output must not be a symlink")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination.parent, 0o700)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(destination)
    os.chmod(destination, 0o600)
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    )
    font_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if font_path is None:
        raise RuntimeError("a stable CJK font is required for the self-authored OCR fixture")
    return ImageFont.truetype(str(font_path), size=size)


def _build_self_authored_fixture(root: Path) -> tuple[Path, Path]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    source = root / "self-authored-bilingual.png"
    gold = root / "self-authored-bilingual.gold.json"
    image = Image.new("RGB", (1800, 1000), "white")
    draw = ImageDraw.Draw(image)
    font = _load_font(64)
    lines = (
        ("Operating Systems", 100, 120),
        ("进程调度与信号量", 100, 360),
        ("Round robin scheduling", 100, 600),
    )
    blocks: list[dict[str, object]] = []
    for order, (line, x, y) in enumerate(lines):
        left, top, right, bottom = draw.textbbox((x, y), line, font=font)
        draw.text((x, y), line, fill="black", font=font)
        padding = 8
        left = max(0, left - padding)
        top = max(0, top - padding)
        right = min(image.width, right + padding)
        bottom = min(image.height, bottom + padding)
        blocks.append(
            {
                "id": f"self-block-{order + 1}",
                "text": line,
                "reading_order": order,
                "bbox_norm": {
                    "x": left / image.width,
                    "y": top / image.height,
                    "width": (right - left) / image.width,
                    "height": (bottom - top) / image.height,
                },
                "kind": "text",
            }
        )
    image.save(source, format="PNG")
    os.chmod(source, 0o600)
    _write_private_json(
        gold,
        {
            "schema_version": "1.0",
            "pages": [
                {
                    "schema_version": "1.0",
                    "page_ordinal": 1,
                    "text": "\n".join(line for line, _x, _y in lines),
                    "blocks": blocks,
                }
            ],
        },
    )
    return source, gold


def _capability_report(executable: Path, cache: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(executable), "capabilities", "--cache-root", str(cache)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("isolated Paddle capability probe is not ready")
    report = json.loads(result.stdout)
    if not isinstance(report, dict) or report.get("ready") is not True:
        raise RuntimeError("isolated Paddle capability probe is not ready")
    return report


def _process_tree_rss(root_pid: int) -> int:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    if result.returncode != 0:
        return 0
    rows: dict[int, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, parent, rss_kib = (int(field) for field in fields)
        except ValueError:
            continue
        rows[pid] = (parent, rss_kib)
    active = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _rss) in rows.items():
            if parent in active and pid not in active:
                active.add(pid)
                changed = True
    return sum(rows.get(pid, (0, 0))[1] for pid in active) * 1024


def _run_profile(
    executable: Path,
    cache: Path,
    source: Path,
    *,
    work_root: Path,
    timeout_seconds: float,
) -> tuple[dict[str, Any], float, int]:
    with tempfile.TemporaryDirectory(prefix="live-ocr-", dir=work_root) as temporary:
        sandbox = Path(temporary)
        os.chmod(sandbox, 0o700)
        input_path = sandbox / "input.png"
        output_dir = sandbox / "output"
        output_dir.mkdir(mode=0o700)
        shutil.copyfile(source, input_path)
        os.chmod(input_path, 0o600)
        document_sha256 = _sha256(input_path)
        stdout_path = sandbox / "stdout.log"
        stderr_path = sandbox / "stderr.log"
        environment = {
            "HOME": str(sandbox),
            "TMPDIR": str(sandbox),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        command = (
            str(executable),
            "run",
            "--backend",
            "general",
            "--input",
            "input.png",
            "--input-sha256",
            document_sha256,
            "--document-sha256",
            document_sha256,
            "--page-index",
            "0",
            "--cache-root",
            str(cache),
            "--max-pages",
            "10",
            "--max-pixels",
            "20000000",
            "--output",
            "output/result.json",
        )
        started = time.perf_counter()
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                cwd=sandbox,
                env=environment,
                start_new_session=True,
                close_fds=True,
            )
            peak_rss_bytes = 0
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                peak_rss_bytes = max(peak_rss_bytes, _process_tree_rss(process.pid))
                if time.monotonic() >= deadline:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                    raise TimeoutError("isolated Paddle OCR exceeded its local benchmark timeout")
                time.sleep(0.05)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if process.returncode != 0:
            raise RuntimeError("isolated Paddle OCR failed")
        result_path = output_dir / "result.json"
        if result_path.is_symlink() or not result_path.is_file():
            raise RuntimeError("isolated Paddle OCR result is unavailable")
        envelope = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or not isinstance(envelope.get("page"), dict):
            raise RuntimeError("isolated Paddle OCR result is invalid")
        return envelope, duration_ms, peak_rss_bytes


def _normalized_page(envelope: dict[str, Any]) -> Page:
    source_version = envelope.get("source_version")
    if not isinstance(source_version, str) or not source_version:
        raise RuntimeError("isolated Paddle OCR source version is unavailable")
    return normalize_paddle_general_output(
        envelope["page"],
        page_ordinal=1,
        raw_result_ref="local-live-ocr-observation",
        source_version=source_version,
    )


def _self_authored_benchmark(
    executable: Path,
    cache: Path,
    output_root: Path,
    capability: dict[str, Any],
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    fixture_root = output_root / "fixtures"
    observations_root = output_root / "observations"
    observations_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(observations_root, 0o700)
    source, gold = _build_self_authored_fixture(fixture_root)
    envelope, duration_ms, peak_rss_bytes = _run_profile(
        executable,
        cache,
        source,
        work_root=output_root,
        timeout_seconds=timeout_seconds,
    )
    page = _normalized_page(envelope)
    if not page.blocks:
        raise RuntimeError("live Paddle OCR returned no normalized blocks")
    entry = OcrEvalEntry(
        id="self-authored-bilingual",
        source_path=str(source.resolve()),
        sha256=_sha256(source),
        size_bytes=source.stat().st_size,
        media_type="image/png",
        split="test",
        privacy="public",
        license_status="self-authored",
        gold_path=str(gold.resolve()),
    )
    manifest = OcrEvalManifest(generated_at=datetime.now(UTC), entries=[entry])
    manifest_path = _write_private_json(
        output_root / "self-authored-manifest.json", manifest.model_dump(mode="json")
    )
    versions = capability.get("versions")
    if not isinstance(versions, dict) or not all(
        isinstance(name, str) and isinstance(version, str) for name, version in versions.items()
    ):
        raise RuntimeError("isolated Paddle dependency versions are invalid")
    parser_version = versions.get("paddleocr")
    if not isinstance(parser_version, str):
        raise RuntimeError("isolated PaddleOCR version is unavailable")
    observation = ExternalObservation(
        entry_id=entry.id,
        execution_mode="live-model",
        parser_backend="paddleocr-general",
        parser_version=parser_version,
        dependency_versions=versions,
        duration_ms=duration_ms,
        peak_rss_bytes=peak_rss_bytes,
        cache_hit=True,
        pages=[page],
    )
    observation_path = _write_private_json(
        observations_root / f"{entry.id}.json", observation.model_dump(mode="json")
    )
    report = run_benchmark(manifest_path, observations_root.resolve())
    report_path = write_report(report, output_root / "live-benchmark.json")
    serialized = report_path.read_text(encoding="utf-8")
    if (
        not report.live_ocr_verified
        or report.contains_raw_text
        or str(source) in serialized
        or str(gold) in serialized
        or any(block.text in serialized for block in page.blocks)
    ):
        raise RuntimeError("OCR benchmark report privacy or live-model verification failed")
    return {
        "manifest": manifest_path,
        "observation": observation_path,
        "report": report_path,
        "duration_ms": duration_ms,
        "peak_rss_bytes": peak_rss_bytes,
        "quality": report.quality_mean,
    }


def _private_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        suffix = candidate.suffix.casefold()
        if suffix in _IMAGE_EXTENSIONS or suffix == ".pdf":
            candidates.append(candidate.resolve(strict=True))
    return sorted(candidates, key=lambda item: (item.stat().st_size, item.name.casefold()))


def _render_private_candidate(candidate: Path, destination: Path) -> Path:
    if candidate.suffix.casefold() in _IMAGE_EXTENSIONS:
        with Image.open(candidate) as image:
            image.convert("RGB").save(destination, format="PNG")
        os.chmod(destination, 0o600)
        return destination
    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise RuntimeError("PDF renderer is unavailable")
    prefix = destination.with_suffix("")
    result = subprocess.run(
        [
            renderer,
            "-f",
            "1",
            "-singlefile",
            "-png",
            "-r",
            "160",
            str(candidate),
            str(prefix),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
    )
    rendered = prefix.with_suffix(".png")
    if result.returncode != 0 or rendered.is_symlink() or not rendered.is_file():
        raise RuntimeError("private PDF first page could not be rendered")
    os.chmod(rendered, 0o600)
    return rendered


def _private_smoke(
    executable: Path,
    cache: Path,
    output_root: Path,
    private_root: Path,
    *,
    timeout_seconds: float,
) -> Path:
    candidates = _private_candidates(private_root)
    attempted = 0
    succeeded = 0
    duration_ms = 0.0
    peak_rss_bytes = 0
    failed_pages: list[int] = []
    with tempfile.TemporaryDirectory(prefix="private-ocr-", dir=output_root) as temporary:
        temporary_root = Path(temporary)
        os.chmod(temporary_root, 0o700)
        for candidate in candidates[:3]:
            attempted += 1
            try:
                source = _render_private_candidate(
                    candidate, temporary_root / f"candidate-{attempted}.png"
                )
                envelope, case_duration_ms, case_peak_rss = _run_profile(
                    executable,
                    cache,
                    source,
                    work_root=temporary_root,
                    timeout_seconds=timeout_seconds,
                )
                page = _normalized_page(envelope)
                duration_ms += case_duration_ms
                peak_rss_bytes = max(peak_rss_bytes, case_peak_rss)
                if not page.blocks:
                    failed_pages.append(1)
                    continue
                succeeded = 1
                break
            except (OSError, RuntimeError, TimeoutError, ValueError):
                failed_pages.append(1)
    return _write_private_json(
        output_root / "private-authorized-smoke.json",
        {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "authorized": True,
            "executed": attempted > 0,
            "attempted_cases": attempted,
            "succeeded_cases": succeeded,
            "failed_pages": sorted(set(failed_pages)) if not succeeded else [],
            "duration_ms": round(duration_ms, 3),
            "peak_rss_bytes": peak_rss_bytes,
            "contains_raw_text": False,
            "contains_source_paths": False,
            "production_readiness": "not-assessed",
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-bin", type=Path, default=_DEFAULT_PROFILE)
    parser.add_argument("--cache-root", type=Path, default=_DEFAULT_CACHE)
    parser.add_argument("--output-root", type=Path, default=_DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--private-source-root", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    arguments = parser.parse_args()
    if arguments.timeout_seconds <= 0:
        raise ValueError("timeout must be positive")
    executable = _private_file(arguments.profile_bin, executable=True)
    cache = _private_cache(arguments.cache_root)
    output_root = arguments.output_root.expanduser().absolute()
    if output_root.is_symlink():
        raise ValueError("output root must not be a symlink")
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)
    capability = _capability_report(executable, cache)
    result = _self_authored_benchmark(
        executable,
        cache,
        output_root,
        capability,
        timeout_seconds=arguments.timeout_seconds,
    )
    private_smoke = None
    if arguments.private_source_root is not None:
        private_smoke = _private_smoke(
            executable,
            cache,
            output_root,
            _private_directory(arguments.private_source_root),
            timeout_seconds=arguments.timeout_seconds,
        )
    print("paddle_capability_ready=true")
    print("live_ocr_verified=true")
    print(f"benchmark_report={result['report']}")
    if private_smoke is not None:
        print("private_authorized_smoke_executed=true")
        print(f"private_smoke_report={private_smoke}")
    print("production_readiness=not-assessed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
