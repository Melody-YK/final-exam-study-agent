"""Fail-closed capability probe and isolated Paddle page execution."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

_EXPECTED_PACKAGES = {
    "paddlepaddle": "paddle",
    "paddleocr": "paddleocr",
    "paddlex": "paddlex",
}
_BACKENDS = ("general", "pp-structure-v3")
_SHA256_CHARS = frozenset("0123456789abcdef")
_READY_MARKERS = {
    "general": ".study-agent-general-ready",
    "pp-structure-v3": ".study-agent-pp-structure-v3-ready",
}
_REQUIRED_MODEL_FILES = ("inference.json", "inference.pdiparams", "inference.yml")
_BACKEND_MODELS: dict[str, dict[str, str]] = {
    "general": {
        "text_detection_model_dir": "official_models/PP-OCRv5_server_det",
        "text_recognition_model_dir": "official_models/PP-OCRv5_server_rec",
    },
    "pp-structure-v3": {
        "layout_detection_model_dir": "official_models/PP-DocLayout_plus-L",
        "region_detection_model_dir": "official_models/PP-DocBlockLayout",
        "doc_orientation_classify_model_dir": "official_models/PP-LCNet_x1_0_doc_ori",
        "doc_unwarping_model_dir": "official_models/UVDoc",
        "text_detection_model_dir": "official_models/PP-OCRv5_server_det",
        "textline_orientation_model_dir": "official_models/PP-LCNet_x1_0_textline_ori",
        "text_recognition_model_dir": "official_models/PP-OCRv5_server_rec",
        "table_classification_model_dir": "official_models/PP-LCNet_x1_0_table_cls",
        "wired_table_structure_recognition_model_dir": "official_models/SLANeXt_wired",
        "wireless_table_structure_recognition_model_dir": "official_models/SLANet_plus",
        "wired_table_cells_detection_model_dir": ("official_models/RT-DETR-L_wired_table_cell_det"),
        "wireless_table_cells_detection_model_dir": (
            "official_models/RT-DETR-L_wireless_table_cell_det"
        ),
        "formula_recognition_model_dir": "official_models/PP-FormulaNet_plus-L",
    },
}

Predictor = Callable[[str, Path, Path], list[object]]


class ProfileExecutionError(RuntimeError):
    """Sanitized failure emitted by the isolated executable."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def capability_report(*, cache_root: Path | None = None) -> dict[str, Any]:
    """Report only locally verified capability facts; never initialize or download a model."""

    resolved_cache = (cache_root or _default_cache_root()).expanduser().resolve()
    versions: dict[str, str] = {}
    missing_packages: list[str] = []
    for distribution, module in _EXPECTED_PACKAGES.items():
        if importlib.util.find_spec(module) is None:
            missing_packages.append(distribution)
            continue
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            missing_packages.append(distribution)

    cached_files = _count_model_files(resolved_cache)
    platform_supported = platform.machine() == "arm64" and sys.version_info[:2] == (3, 12)
    packages_available = not missing_packages
    models_available = _marker_ready(resolved_cache, "general", versions=versions)
    ready = platform_supported and packages_available and models_available
    if not platform_supported:
        reason_code = "OCR_PLATFORM_UNSUPPORTED"
    elif not packages_available:
        reason_code = "OCR_PROFILE_NOT_INSTALLED"
    elif not models_available:
        reason_code = "OCR_MODELS_NOT_CACHED"
    else:
        reason_code = None

    return {
        "schema_version": "1.0",
        "profile": "paddle-ocr-v1",
        "ready": ready,
        "reason_code": reason_code,
        "platform": platform.machine(),
        "python": platform.python_version(),
        "versions": versions,
        "missing_packages": sorted(missing_packages),
        "cache_root": str(resolved_cache),
        "cached_file_count": cached_files,
        "supports_ocr": ready,
        "supports_pp_structure": ready
        and _marker_ready(resolved_cache, "pp-structure-v3", versions=versions),
        "supports_mineru": False,
        "supports_paid_ocr": False,
    }


def execute_page(
    *,
    backend: str,
    input_path: Path,
    page_index: int,
    cache_root: Path,
    document_sha256: str,
    max_pages: int,
    max_pixels: int,
    predictor: Predictor | None = None,
    versions: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Execute one isolated engine and return one strict, path-free page envelope."""

    if backend not in _BACKENDS:
        raise ProfileExecutionError("OCR_BACKEND_DISABLED")
    if page_index < 0 or max_pages <= 0 or max_pixels <= 0:
        raise ProfileExecutionError("OCR_REQUEST_INVALID")
    if not _valid_sha256(document_sha256):
        raise ProfileExecutionError("OCR_DOCUMENT_HASH_INVALID")

    prediction = predictor or _predict_with_paddle
    try:
        raw_results = prediction(backend, input_path, cache_root)
    except MemoryError:
        raise ProfileExecutionError("OCR_OUT_OF_MEMORY", retryable=True) from None
    except ProfileExecutionError:
        raise
    except Exception:
        raise ProfileExecutionError("OCR_ENGINE_FAILED", retryable=True) from None
    if not raw_results or len(raw_results) > max_pages:
        raise ProfileExecutionError("OCR_PAGE_COUNT_INVALID")

    pages = [
        _canonical_page(backend, item, fallback_page_index=index, input_path=input_path)
        for index, item in enumerate(raw_results)
    ]
    indices = [page.get("page_index") for page in pages]
    if len(indices) != len(set(indices)) or any(
        not isinstance(index, int) or index < 0 or index >= len(pages) for index in indices
    ):
        raise ProfileExecutionError("OCR_PAGE_COUNT_INVALID")
    selected = next((page for page in pages if page["page_index"] == page_index), None)
    if selected is None:
        raise ProfileExecutionError("OCR_PAGE_OUT_OF_RANGE")
    width = selected.get("image_width")
    height = selected.get("image_height")
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not isinstance(height, int)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
        or width * height > max_pixels
    ):
        raise ProfileExecutionError("OCR_PIXEL_LIMIT_EXCEEDED")

    resolved_versions = dict(versions or _profile_versions())
    source_version = ";".join(
        f"{name}={value}" for name, value in sorted(resolved_versions.items())
    )
    if not source_version:
        raise ProfileExecutionError("OCR_PROFILE_VERSION_UNAVAILABLE")
    return {
        "schema_version": "1.0",
        "document_sha256": document_sha256,
        "backend": backend,
        "source_version": source_version,
        "total_page_count": len(pages),
        "page": selected,
    }


def main(argv: Sequence[str] | None = None, *, predictor: Predictor | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "capabilities":
        report = capability_report(cache_root=arguments.cache_root)
        print(json.dumps(report, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0 if report["ready"] else 3
    if arguments.command == "warmup":
        try:
            cache_root = _warmup_cache_path(arguments.cache_root)
            _initialize_backend(arguments.backend, cache_root)
            _write_ready_marker(cache_root, arguments.backend)
            print(
                json.dumps(
                    {"backend": arguments.backend, "warmed": True},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        except ProfileExecutionError as exc:
            print(
                json.dumps(
                    {"code": exc.code, "retryable": exc.retryable},
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 2
        except Exception:
            print('{"code":"OCR_WARMUP_FAILED","retryable":true}')
            return 3

    try:
        input_path = _sandbox_path(arguments.input, must_exist=True)
        output_path = _sandbox_path(arguments.output, must_exist=False)
        cache_root = _cache_path(arguments.cache_root)
        if _sha256(input_path) != arguments.input_sha256:
            raise ProfileExecutionError("OCR_INPUT_HASH_MISMATCH")
        if predictor is None:
            report = capability_report(cache_root=cache_root)
            if not report["ready"]:
                raise ProfileExecutionError(str(report["reason_code"]), retryable=True)
            if not _marker_ready(cache_root, arguments.backend, versions=_profile_versions()):
                raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
        result = execute_page(
            backend=arguments.backend,
            input_path=input_path,
            page_index=arguments.page_index,
            cache_root=cache_root,
            document_sha256=arguments.document_sha256,
            max_pages=arguments.max_pages,
            max_pixels=arguments.max_pixels,
            predictor=predictor,
        )
        _write_private_json(output_path, result)
        return 0
    except ProfileExecutionError as exc:
        print(
            json.dumps(
                {"code": exc.code, "retryable": exc.retryable},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    except Exception:
        print('{"code":"OCR_CHILD_FAILED","retryable":true}')
        return 3


def _predict_with_paddle(backend: str, input_path: Path, cache_root: Path) -> list[object]:
    """Import Paddle only inside the isolated profile process."""

    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_root)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["HF_HUB_OFFLINE"] = "1"
    model_kwargs = _verified_model_kwargs(cache_root, backend)
    if backend == "general":
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]

        engine = PaddleOCR(
            **model_kwargs,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    elif backend == "pp-structure-v3":
        from paddleocr import PPStructureV3

        engine = PPStructureV3(**model_kwargs)
    else:
        raise ProfileExecutionError("OCR_BACKEND_DISABLED")
    return list(engine.predict(input=str(input_path)))


def _initialize_backend(backend: str, cache_root: Path) -> None:
    """Initialize and execute one self-authored page before writing a ready marker."""

    os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_root)
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    model_kwargs = _local_model_kwargs(cache_root, backend)
    if backend == "general":
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]

        engine = PaddleOCR(
            **model_kwargs,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        _verify_initialized_engine(backend, engine, cache_root)
        return
    if backend == "pp-structure-v3":
        from paddleocr import PPStructureV3

        engine = PPStructureV3(**model_kwargs)
        _verify_initialized_engine(backend, engine, cache_root)
        return
    raise ProfileExecutionError("OCR_BACKEND_DISABLED")


def _verify_initialized_engine(backend: str, engine: object, cache_root: Path) -> None:
    predict = getattr(engine, "predict", None)
    if not callable(predict):
        raise ProfileExecutionError("OCR_ENGINE_FAILED", retryable=True)
    try:
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory(prefix="profile-warmup-", dir=cache_root) as temporary:
            source = Path(temporary) / "self-authored.png"
            image = Image.new("RGB", (800, 300), "white")
            ImageDraw.Draw(image).text((40, 100), "Study Agent OCR 123", fill="black")
            image.save(source, format="PNG")
            results = list(predict(input=str(source)))
            if not results:
                raise ProfileExecutionError("OCR_ENGINE_FAILED", retryable=True)
            _canonical_page(backend, results[0], fallback_page_index=0, input_path=source)
    except ProfileExecutionError:
        raise
    except Exception:
        raise ProfileExecutionError("OCR_ENGINE_FAILED", retryable=True) from None


def _canonical_page(
    backend: str,
    value: object,
    *,
    fallback_page_index: int,
    input_path: Path,
) -> dict[str, object]:
    raw = _result_mapping(value)
    payload_value = raw.get("res", raw)
    if not isinstance(payload_value, Mapping):
        raise ProfileExecutionError("OCR_RESULT_INVALID")
    payload = {str(key): item for key, item in payload_value.items()}
    page_index = _plain(payload.get("page_index"))
    payload["page_index"] = fallback_page_index if page_index is None else page_index
    width, height = _image_dimensions(value, raw, payload, input_path=input_path)
    payload["image_width"] = width
    payload["image_height"] = height
    if backend == "general":
        required = ("dt_polys", "rec_texts", "rec_scores")
        if any(key not in payload for key in required):
            raise ProfileExecutionError("OCR_RESULT_INVALID")
        payload = {
            **payload,
            "dt_polys": _plain(payload["dt_polys"]),
            "rec_polys": _plain(payload.get("rec_polys")),
            "rec_texts": _plain(payload["rec_texts"]),
            "rec_scores": _plain(payload["rec_scores"]),
        }
        return {
            key: payload[key]
            for key in (
                *required[:1],
                "rec_polys",
                *required[1:],
                "page_index",
                "image_width",
                "image_height",
            )
        }
    if "parsing_res_list" not in payload:
        raise ProfileExecutionError("OCR_RESULT_INVALID")
    raw_blocks = payload["parsing_res_list"]
    if not isinstance(raw_blocks, list):
        raise ProfileExecutionError("OCR_RESULT_INVALID")
    blocks: list[dict[str, object]] = []
    for raw_block in raw_blocks:
        raw_block_mapping = _result_mapping(raw_block)
        block_value = raw_block_mapping.get("res", raw_block_mapping)
        if not isinstance(block_value, Mapping):
            raise ProfileExecutionError("OCR_RESULT_INVALID")
        block = {str(key): item for key, item in block_value.items()}
        if not all(key in block for key in ("block_label", "block_content", "block_bbox")):
            raise ProfileExecutionError("OCR_RESULT_INVALID")
        blocks.append(
            {
                "block_label": _plain(block["block_label"]),
                "block_content": _plain(block["block_content"]),
                "block_bbox": _plain(block["block_bbox"]),
                "block_order": _plain(block.get("block_order")),
                "score": _plain(block.get("score", block.get("block_score", 0.0))),
            }
        )
    return {
        "page_index": payload["page_index"],
        "image_width": payload["image_width"],
        "image_height": payload["image_height"],
        "parsing_res_list": blocks,
    }


def _result_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    candidate = getattr(value, "json", None)
    if callable(candidate):
        candidate = candidate()
    if candidate is None:
        to_json = getattr(value, "to_json", None)
        candidate = to_json() if callable(to_json) else None
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            raise ProfileExecutionError("OCR_RESULT_INVALID") from None
    if not isinstance(candidate, Mapping):
        raise ProfileExecutionError("OCR_RESULT_INVALID")
    return {str(key): item for key, item in candidate.items()}


def _image_dimensions(
    original: object,
    raw: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    input_path: Path,
) -> tuple[object, object]:
    width = payload.get("image_width", payload.get("width"))
    height = payload.get("image_height", payload.get("height"))
    if width is not None and height is not None:
        return width, height
    for candidate in (
        raw.get("input_img"),
        payload.get("input_img"),
        _nested_image(raw.get("doc_preprocessor_res")),
        _nested_image(payload.get("doc_preprocessor_res")),
        getattr(original, "img", None),
    ):
        shape = getattr(candidate, "shape", None)
        if isinstance(shape, tuple | list) and len(shape) >= 2:
            return _plain(shape[1]), _plain(shape[0])
    try:
        from PIL import Image

        with Image.open(input_path) as image:
            return image.width, image.height
    except Exception:
        raise ProfileExecutionError("OCR_RESULT_INVALID") from None


def _nested_image(value: object) -> object:
    if not isinstance(value, Mapping):
        return None
    return value.get("output_img", value.get("input_img"))


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    to_list = getattr(value, "tolist", None)
    if callable(to_list):
        return _plain(to_list())
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise ProfileExecutionError("OCR_RESULT_INVALID")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capabilities = subparsers.add_parser("capabilities", add_help=False, allow_abbrev=False)
    capabilities.add_argument("--cache-root", type=Path)
    warmup = subparsers.add_parser("warmup", add_help=False, allow_abbrev=False)
    warmup.add_argument("--backend", choices=_BACKENDS, required=True)
    warmup.add_argument("--cache-root", type=Path, required=True)
    run = subparsers.add_parser("run", add_help=False, allow_abbrev=False)
    run.add_argument("--backend", choices=_BACKENDS, required=True)
    run.add_argument("--input", required=True)
    run.add_argument("--input-sha256", required=True)
    run.add_argument("--document-sha256", required=True)
    run.add_argument("--page-index", type=int, required=True)
    run.add_argument("--cache-root", type=Path, required=True)
    run.add_argument("--max-pages", type=int, required=True)
    run.add_argument("--max-pixels", type=int, required=True)
    run.add_argument("--output", required=True)
    return parser


def _sandbox_path(value: str, *, must_exist: bool) -> Path:
    pure_path = PurePosixPath(value)
    if (
        pure_path.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ProfileExecutionError("OCR_CHILD_PATH_INVALID")
    root = Path.cwd().resolve(strict=True)
    current = root
    for part in pure_path.parts:
        current /= part
        if current.is_symlink():
            raise ProfileExecutionError("OCR_CHILD_PATH_INVALID")
    candidate = (root / Path(*pure_path.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ProfileExecutionError("OCR_CHILD_PATH_INVALID") from None
    if must_exist and (candidate.is_symlink() or not candidate.is_file()):
        raise ProfileExecutionError("OCR_CHILD_INPUT_MISSING", retryable=True)
    return candidate


def _cache_path(value: Path) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    try:
        resolved = expanded.resolve(strict=True)
    except OSError:
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True) from None
    if not resolved.is_dir() or not _count_model_files(resolved):
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    return resolved


def _warmup_cache_path(value: Path) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink():
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    expanded.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        resolved = expanded.resolve(strict=True)
    except OSError:
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True) from None
    if not resolved.is_dir():
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    os.chmod(resolved, 0o700)
    return resolved


def _write_ready_marker(cache_root: Path, backend: str) -> None:
    marker_name = _READY_MARKERS.get(backend)
    if marker_name is None:
        raise ProfileExecutionError("OCR_BACKEND_DISABLED")
    marker = cache_root / marker_name
    if marker.is_symlink():
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    temporary = marker.with_suffix(".tmp")
    models = _model_snapshot(cache_root, backend)
    payload = {
        "schema_version": "1.0",
        "backend": backend,
        "profile": "paddle-ocr-v1",
        "versions": _profile_versions(),
        "models": models,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, marker)
    os.chmod(marker, 0o600)


def _marker_ready(
    cache_root: Path,
    backend: str,
    *,
    versions: Mapping[str, str] | None = None,
) -> bool:
    marker_name = _READY_MARKERS.get(backend)
    if marker_name is None:
        return False
    marker = cache_root / marker_name
    if marker.is_symlink() or not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        expected_versions = dict(versions or _profile_versions())
        return (
            isinstance(payload, dict)
            and payload.get("schema_version") == "1.0"
            and payload.get("backend") == backend
            and payload.get("profile") == "paddle-ocr-v1"
            and payload.get("versions") == expected_versions
            and payload.get("models") == _model_snapshot(cache_root, backend)
        )
    except (OSError, ValueError, json.JSONDecodeError, ProfileExecutionError):
        return False


def _model_snapshot(cache_root: Path, backend: str) -> dict[str, object]:
    configured = _BACKEND_MODELS.get(backend)
    if configured is None:
        raise ProfileExecutionError("OCR_BACKEND_DISABLED")
    models: dict[str, object] = {}
    for relative in sorted(set(configured.values())):
        directory = cache_root / relative
        if directory.is_symlink() or not directory.is_dir():
            raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
        files: dict[str, object] = {}
        for name in _REQUIRED_MODEL_FILES:
            candidate = directory / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
            size = candidate.stat().st_size
            if size <= 0:
                raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
            files[name] = {"sha256": _sha256(candidate), "size_bytes": size}
        models[relative] = files
    return models


def _verified_model_kwargs(cache_root: Path, backend: str) -> dict[str, str]:
    if not _marker_ready(cache_root, backend, versions=_profile_versions()):
        raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
    return _local_model_kwargs(cache_root, backend)


def _local_model_kwargs(cache_root: Path, backend: str) -> dict[str, str]:
    """Bind PaddleX to the verified local model name and directory pair."""

    configured = _BACKEND_MODELS.get(backend)
    if configured is None:
        raise ProfileExecutionError("OCR_BACKEND_DISABLED")
    _model_snapshot(cache_root, backend)
    kwargs: dict[str, str] = {}
    for directory_argument, relative in configured.items():
        directory = (cache_root / relative).resolve(strict=True)
        kwargs[directory_argument] = str(directory)
        if not directory_argument.endswith("_dir"):
            raise ProfileExecutionError("OCR_MODELS_NOT_CACHED", retryable=True)
        kwargs[directory_argument.removesuffix("_dir") + "_name"] = directory.name
    return kwargs


def _write_private_json(path: Path, payload: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _profile_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in _EXPECTED_PACKAGES:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def _default_cache_root() -> Path:
    configured = os.environ.get("PADDLE_PDX_CACHE_HOME", "").strip()
    return Path(configured) if configured else Path.home() / ".paddlex"


def _count_model_files(cache_root: Path) -> int:
    if cache_root.is_symlink() or not cache_root.is_dir():
        return 0
    count = 0
    try:
        paths = cache_root.rglob("*")
        for path in paths:
            if path.is_symlink():
                continue
            try:
                if (
                    path.is_file()
                    and path.name not in _READY_MARKERS.values()
                    and path.stat().st_size > 0
                ):
                    count += 1
            except OSError:
                continue
    except OSError:
        return 0
    return count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in _SHA256_CHARS for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
