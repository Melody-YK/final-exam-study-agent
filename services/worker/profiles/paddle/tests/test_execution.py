from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from study_worker_paddle import cli


def _create_backend_models(cache: Path, backend: str) -> None:
    for directory in cli._BACKEND_MODELS[backend].values():
        model = cache / directory
        model.mkdir(parents=True, exist_ok=True)
        for name in cli._REQUIRED_MODEL_FILES:
            (model / name).write_bytes(f"{backend}:{directory}:{name}".encode())


def _general_page(page_index: int) -> dict[str, object]:
    return {
        "page_index": page_index,
        "image_width": 800,
        "image_height": 600,
        "dt_polys": [[[40, 50], [500, 50], [500, 120], [40, 120]]],
        "rec_polys": None,
        "rec_texts": [f"self-authored page {page_index + 1}"],
        "rec_scores": [0.98],
    }


def test_execute_page_selects_requested_general_result_without_importing_paddle(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"self-authored-image-fixture")
    cache = tmp_path / "models"
    _create_backend_models(cache, "general")

    calls: list[tuple[str, Path, Path]] = []

    def predictor(backend: str, input_path: Path, cache_root: Path) -> list[object]:
        calls.append((backend, input_path, cache_root))
        return [_general_page(0), _general_page(1)]

    result = cli.execute_page(
        backend="general",
        input_path=source,
        page_index=1,
        cache_root=cache,
        document_sha256="a" * 64,
        max_pages=10,
        max_pixels=1_000_000,
        predictor=predictor,
        versions={"paddleocr": "3.7.0-test", "paddlepaddle": "3.3.1-test"},
    )

    assert calls == [("general", source, cache)]
    assert result["backend"] == "general"
    assert result["total_page_count"] == 2
    assert result["page"] == _general_page(1)
    assert result["source_version"] == "paddleocr=3.7.0-test;paddlepaddle=3.3.1-test"


def test_warmup_command_initializes_only_requested_backend_and_writes_marker(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    cache = tmp_path / "models"
    _create_backend_models(cache, "general")
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        cli,
        "_initialize_backend",
        lambda backend, cache_root: calls.append((backend, cache_root)),
    )

    exit_code = cli.main(["warmup", "--backend", "general", "--cache-root", str(cache.absolute())])

    assert exit_code == 0
    assert calls == [("general", cache.absolute())]
    marker = json.loads((cache / ".study-agent-general-ready").read_text(encoding="utf-8"))
    assert marker["schema_version"] == "1.0"
    assert marker["backend"] == "general"
    assert marker["models"]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"backend": "general", "warmed": True}


def test_local_model_kwargs_bind_each_directory_to_its_cached_model_name(tmp_path: Path) -> None:
    cache = tmp_path / "models"
    _create_backend_models(cache, "general")

    kwargs = cli._local_model_kwargs(cache, "general")

    assert kwargs["text_detection_model_name"] == "PP-OCRv5_server_det"
    assert kwargs["text_recognition_model_name"] == "PP-OCRv5_server_rec"
    assert kwargs["text_detection_model_dir"] == str(
        (cache / "official_models" / "PP-OCRv5_server_det").resolve()
    )


def test_execute_page_never_materializes_or_emits_vendor_image_arrays(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"self-authored-image-fixture")
    cache = tmp_path / "models"
    cache.mkdir()

    class ForbiddenImageArray:
        shape = (600, 800, 3)

        def tolist(self) -> object:
            raise AssertionError("vendor image arrays must not be materialized")

    payload = {
        **_general_page(0),
        "input_img": ForbiddenImageArray(),
        "private_vendor_debug": ForbiddenImageArray(),
    }

    result = cli.execute_page(
        backend="general",
        input_path=source,
        page_index=0,
        cache_root=cache,
        document_sha256="a" * 64,
        max_pages=10,
        max_pixels=1_000_000,
        predictor=lambda *_args: [payload],
        versions={"paddleocr": "3.7.0-test"},
    )

    assert "input_img" not in result["page"]
    assert "private_vendor_debug" not in result["page"]


def test_run_command_writes_private_pp_structure_result_with_injected_engine(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"self-authored-structure-fixture")
    cache = tmp_path / "models"
    cache.mkdir()
    (cache / "model.pdparams").write_bytes(b"model-marker")

    pp_page = {
        "page_index": 0,
        "image_width": 900,
        "image_height": 700,
        "parsing_res_list": [
            {
                "block_label": "table",
                "block_content": "A | B",
                "block_bbox": [50, 80, 850, 500],
                "block_order": 0,
                "score": 0.9,
            }
        ],
    }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli,
        "_profile_versions",
        lambda: {"paddleocr": "3.7.0-test", "paddlepaddle": "3.3.1-test"},
    )

    exit_code = cli.main(
        [
            "run",
            "--backend",
            "pp-structure-v3",
            "--input",
            "input.bin",
            "--input-sha256",
            hashlib.sha256(source.read_bytes()).hexdigest(),
            "--document-sha256",
            "b" * 64,
            "--page-index",
            "0",
            "--cache-root",
            str(cache),
            "--max-pages",
            "5",
            "--max-pixels",
            "1000000",
            "--output",
            "output/result.json",
        ],
        predictor=lambda *_args: [pp_page],
    )

    output = tmp_path / "output" / "result.json"
    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["page"] == pp_page
    assert os.stat(output).st_mode & 0o777 == 0o600


def test_execute_page_accepts_pp_structure_vendor_width_and_height_keys(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"self-authored-structure-image")
    cache = tmp_path / "models"
    cache.mkdir()
    payload = {
        "page_index": None,
        "width": 900,
        "height": 700,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content": "self-authored structure text",
                "block_bbox": [50, 80, 850, 500],
                "block_order": 0,
            }
        ],
    }

    result = cli.execute_page(
        backend="pp-structure-v3",
        input_path=source,
        page_index=0,
        cache_root=cache,
        document_sha256="a" * 64,
        max_pages=5,
        max_pixels=1_000_000,
        predictor=lambda *_args: [payload],
        versions={"paddleocr": "3.7.0-test"},
    )

    assert result["page"]["image_width"] == 900
    assert result["page"]["image_height"] == 700
    assert result["page"]["parsing_res_list"][0]["score"] == 0.0


def test_execute_page_accepts_pp_structure_vendor_block_result_objects(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"self-authored-structure-image")
    cache = tmp_path / "models"
    cache.mkdir()

    class VendorBlockResult:
        @property
        def json(self) -> dict[str, object]:
            return {
                "res": {
                    "block_label": "text",
                    "block_content": "self-authored structure text",
                    "block_bbox": [50, 80, 850, 500],
                    "block_order": 0,
                    "private_vendor_debug": "must-not-cross-boundary",
                }
            }

    payload = {
        "page_index": None,
        "width": 900,
        "height": 700,
        "parsing_res_list": [VendorBlockResult()],
    }

    result = cli.execute_page(
        backend="pp-structure-v3",
        input_path=source,
        page_index=0,
        cache_root=cache,
        document_sha256="a" * 64,
        max_pages=5,
        max_pixels=1_000_000,
        predictor=lambda *_args: [payload],
        versions={"paddleocr": "3.7.0-test"},
    )

    block = result["page"]["parsing_res_list"][0]
    assert block["block_content"] == "self-authored structure text"
    assert "private_vendor_debug" not in block
