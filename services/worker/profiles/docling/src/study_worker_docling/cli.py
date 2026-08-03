"""Small process boundary around Docling with a vendor-neutral JSON result."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader, PdfWriter

Backend = Literal["standard", "vlm"]
STANDARD_MARKER = ".study-agent-docling-standard-ready.json"
VLM_MARKER = ".study-agent-docling-vlm-ready.json"


class ProfileError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study-agent-docling-profile")
    subparsers = parser.add_subparsers(dest="command", required=True)

    capabilities = subparsers.add_parser("capabilities")
    capabilities.add_argument("--artifacts-root", type=Path, required=True)

    warmup = subparsers.add_parser("warmup")
    warmup.add_argument("--backend", choices=("standard", "vlm"), required=True)
    warmup.add_argument("--artifacts-root", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--backend", choices=("standard", "vlm"), required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--document-sha256", required=True)
    run.add_argument("--page", type=int, required=True)
    run.add_argument("--artifacts-root", type=Path, required=True)
    run.add_argument("--max-pages", type=int, required=True)
    run.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "capabilities":
            report = _capabilities(args.artifacts_root)
            _write_stdout(report)
            raise SystemExit(0 if report["standard_ready"] else 3)
        if args.command == "warmup":
            report = _warmup(args.backend, args.artifacts_root)
            _write_stdout(report)
            return
        _run(
            backend=args.backend,
            input_path=args.input,
            document_sha256=args.document_sha256,
            page=args.page,
            artifacts_root=args.artifacts_root,
            max_pages=args.max_pages,
            output=args.output,
        )
    except ProfileError as exc:
        _write_stdout({"code": exc.code, "retryable": exc.retryable})
        raise SystemExit(3) from None


def _capabilities(artifacts_root: Path) -> dict[str, object]:
    root = _absolute_directory(artifacts_root)
    version = _docling_version()
    if version is None:
        return _capability_report(
            root,
            standard_ready=False,
            standard_reason="DOCLING_PACKAGES_MISSING",
            vlm_ready=False,
            vlm_reason="DOCLING_PACKAGES_MISSING",
            versions={},
        )
    standard_ready = _valid_marker(root / STANDARD_MARKER, "standard", version)
    vlm_ready = standard_ready and _valid_marker(root / VLM_MARKER, "vlm", version)
    return _capability_report(
        root,
        standard_ready=standard_ready,
        standard_reason=None if standard_ready else "DOCLING_STANDARD_NOT_WARMED",
        vlm_ready=vlm_ready,
        vlm_reason=None if vlm_ready else "DOCLING_VLM_NOT_WARMED",
        versions={"docling": version},
    )


def _capability_report(
    root: Path,
    *,
    standard_ready: bool,
    standard_reason: str | None,
    vlm_ready: bool,
    vlm_reason: str | None,
    versions: dict[str, str],
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "profile": "docling-v1",
        "standard_ready": standard_ready,
        "standard_reason_code": standard_reason,
        "vlm_ready": vlm_ready,
        "vlm_reason_code": vlm_reason,
        "versions": versions,
        "artifacts_root": str(root),
    }


def _warmup(backend: Backend, artifacts_root: Path) -> dict[str, object]:
    root = artifacts_root.expanduser().resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ProfileError("DOCLING_ARTIFACTS_UNAVAILABLE")
    version = _docling_version()
    if version is None:
        raise ProfileError("DOCLING_PACKAGES_MISSING")
    if backend == "vlm" and not _valid_marker(root / STANDARD_MARKER, "standard", version):
        raise ProfileError("DOCLING_STANDARD_NOT_WARMED")

    _configure_cache_environment(root, offline=False)
    try:
        converter = _converter(backend, root)
        with tempfile.TemporaryDirectory(prefix="study-agent-docling-") as temporary:
            sample = Path(temporary) / "warmup.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            with sample.open("wb") as stream:
                writer.write(stream)
            converter.convert(sample, page_range=(1, 1))
    except Exception:
        raise ProfileError("DOCLING_WARMUP_FAILED", retryable=True) from None

    marker_name = STANDARD_MARKER if backend == "standard" else VLM_MARKER
    marker = root / marker_name
    marker.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "backend": backend,
                "docling_version": version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    return {"status": "ready", "backend": backend, "docling_version": version}


def _run(
    *,
    backend: Backend,
    input_path: Path,
    document_sha256: str,
    page: int,
    artifacts_root: Path,
    max_pages: int,
    output: Path,
) -> None:
    root = _absolute_directory(artifacts_root)
    version = _docling_version()
    if version is None:
        raise ProfileError("DOCLING_PACKAGES_MISSING")
    marker_name = STANDARD_MARKER if backend == "standard" else VLM_MARKER
    if not _valid_marker(root / marker_name, backend, version):
        raise ProfileError(f"DOCLING_{backend.upper()}_NOT_WARMED")
    if page < 1 or max_pages < 1:
        raise ProfileError("DOCLING_PAGE_REQUEST_INVALID")
    if _sha256(input_path) != document_sha256:
        raise ProfileError("DOCLING_INPUT_HASH_MISMATCH")
    try:
        reader = PdfReader(input_path, strict=False)
        if reader.is_encrypted:
            raise ProfileError("PDF_ENCRYPTED")
        total_pages = len(reader.pages)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError("DOCLING_PDF_INVALID") from None
    if total_pages < 1 or total_pages > max_pages:
        raise ProfileError("DOCLING_PAGE_COUNT_INVALID")
    if page > total_pages:
        raise ProfileError("DOCLING_PAGE_OUT_OF_RANGE")

    _configure_cache_environment(root, offline=True)
    try:
        result = _converter(backend, root).convert(input_path, page_range=(page, page))
        raw_page = _raw_page(result.document, page)
    except Exception:
        raise ProfileError("DOCLING_PARSE_FAILED", retryable=True) from None
    payload = {
        "schema_version": "1.0",
        "document_sha256": document_sha256,
        "parser_profile": "native-v1",
        "source_backend": f"docling-{backend}",
        "source_version": version,
        "total_page_count": total_pages,
        "pages": [raw_page],
    }
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(output, 0o600)


def _converter(backend: Backend, artifacts_root: Path) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, VlmPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if backend == "standard":
        options = PdfPipelineOptions()
        options.artifacts_path = artifacts_root
        options.enable_remote_services = False
        format_option = PdfFormatOption(pipeline_options=options)
    else:
        from docling.pipeline.vlm_pipeline import VlmPipeline

        options = VlmPipelineOptions()
        options.artifacts_path = artifacts_root
        options.enable_remote_services = False
        format_option = PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=options)
    return DocumentConverter(format_options={InputFormat.PDF: format_option})


def _raw_page(document: Any, page_no: int) -> dict[str, object]:
    page_entry = document.pages.get(page_no)
    if page_entry is None:
        raise ProfileError("DOCLING_RESULT_COVERAGE_MISMATCH")
    width = max(1, round(float(page_entry.size.width)))
    height = max(1, round(float(page_entry.size.height)))
    blocks: list[dict[str, object]] = []
    section_path: list[str] = []
    for item, _level in _iterate_items(document, page_no):
        block_type = _block_type(item)
        text = _item_text(item, document, block_type)
        if block_type == "title" and text:
            section_path = [text]
        bbox = _item_bbox(item, page_no=page_no, width=width, height=height)
        metadata = {
            "docling_label": _label(item),
            "docling_self_ref": str(getattr(item, "self_ref", ""))[:500],
        }
        blocks.append(
            {
                "type": block_type,
                "text": text,
                "bbox": bbox,
                "reading_order": len(blocks),
                "confidence": _confidence(item),
                "section_path": list(section_path),
                "metadata": metadata,
                "artifact": None,
            }
        )
    return {
        "ordinal": page_no,
        "width": width,
        "height": height,
        "source_kind": "page",
        "native_text_present": any(str(block["text"]).strip() for block in blocks),
        "blocks": blocks,
        "metadata": {
            "docling_item_count": len(blocks),
            "docling_unresolved_structure_count": sum(
                1
                for block in blocks
                if block["type"] in {"table", "image", "formula"} and not str(block["text"]).strip()
            ),
        },
    }


def _iterate_items(document: Any, page_no: int) -> Iterable[tuple[Any, int]]:
    try:
        return document.iterate_items(page_no=page_no, with_groups=False)
    except TypeError:
        return document.iterate_items(page_no=page_no)


def _label(item: Any) -> str:
    label = getattr(item, "label", "text")
    return str(getattr(label, "value", label)).lower().replace("-", "_")


def _block_type(item: Any) -> str:
    label = _label(item)
    if label in {"title", "section_header", "page_header"}:
        return "title"
    if "table" in label:
        return "table"
    if label in {"formula", "equation"} or "formula" in label:
        return "formula"
    if label in {"picture", "image", "chart"} or "picture" in label:
        return "image"
    if "code" in label:
        return "code"
    return "paragraph"


def _item_text(item: Any, document: Any, block_type: str) -> str:
    if block_type == "table" and hasattr(item, "export_to_markdown"):
        try:
            return str(item.export_to_markdown(document)).strip()
        except Exception:
            pass
    text = str(getattr(item, "text", "") or "").strip()
    if text:
        return text
    if block_type == "image":
        for method_name in ("caption_text", "get_caption_text"):
            method = getattr(item, method_name, None)
            if callable(method):
                try:
                    caption = str(method(document) or "").strip()
                except Exception:
                    continue
                if caption:
                    return caption
        annotations = getattr(item, "annotations", None) or []
        descriptions = [
            str(getattr(annotation, "text", "") or "").strip() for annotation in annotations
        ]
        return "\n".join(value for value in descriptions if value)
    return ""


def _item_bbox(
    item: Any,
    *,
    page_no: int,
    width: int,
    height: int,
) -> dict[str, float]:
    provenance = [
        value for value in (getattr(item, "prov", None) or []) if value.page_no == page_no
    ]
    if not provenance:
        return {"x0": 0.0, "top": 0.0, "x1": float(width), "bottom": float(height)}
    bbox = provenance[0].bbox
    converter = getattr(bbox, "to_top_left_origin", None)
    if callable(converter):
        try:
            bbox = converter(page_height=height)
        except TypeError:
            bbox = converter(height)
    left = _bounded(float(getattr(bbox, "l", 0.0)), 0.0, float(width))
    right = _bounded(float(getattr(bbox, "r", width)), left, float(width))
    top = _bounded(float(getattr(bbox, "t", 0.0)), 0.0, float(height))
    bottom = _bounded(float(getattr(bbox, "b", height)), top, float(height))
    return {"x0": left, "top": top, "x1": right, "bottom": bottom}


def _confidence(item: Any) -> float:
    provenance = getattr(item, "prov", None) or []
    value = getattr(provenance[0], "score", 1.0) if provenance else 1.0
    try:
        return _bounded(float(value), 0.0, 1.0)
    except (TypeError, ValueError):
        return 1.0


def _bounded(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _valid_marker(path: Path, backend: Backend, version: str) -> bool:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_096:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload == {
        "schema_version": "1.0",
        "backend": backend,
        "docling_version": version,
    }


def _absolute_directory(path: Path) -> Path:
    configured = path.expanduser()
    if not configured.is_absolute() or configured.is_symlink():
        raise ProfileError("DOCLING_ARTIFACTS_UNAVAILABLE")
    try:
        resolved = configured.resolve(strict=True)
    except OSError:
        raise ProfileError("DOCLING_ARTIFACTS_UNAVAILABLE") from None
    if not resolved.is_dir():
        raise ProfileError("DOCLING_ARTIFACTS_UNAVAILABLE")
    return resolved


def _configure_cache_environment(root: Path, *, offline: bool) -> None:
    cache = root / "huggingface"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(cache / "hub")
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _docling_version() -> str | None:
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError:
        raise ProfileError("DOCLING_INPUT_UNREADABLE", retryable=True) from None
    return digest.hexdigest()


def _write_stdout(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
