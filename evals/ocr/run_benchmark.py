"""Aggregate external OCR observations into a text-free benchmark report."""

from __future__ import annotations

import argparse
import hashlib
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.ocr.manifest import OcrEvalManifest, OcrGoldBlock, OcrGoldPage
from evals.ocr.metrics import (
    ResourceObservation,
    bbox_iou,
    character_error_rate,
    reading_order_score,
    summarize_resources,
)
from study_contracts import Block, BlockType, Page

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_RAW_LOCAL_ROOTS = (
    _REPOSITORY_ROOT / ".local",
    _REPOSITORY_ROOT / "evals" / "private",
    _REPOSITORY_ROOT / "evals" / "reports" / "generated",
)
_NAME_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_VERSION_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,99}")


class BenchmarkContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExternalObservation(BenchmarkContract):
    """One local adapter result; ``pages`` may contain text and must remain untracked."""

    schema_version: Literal["1.0"] = "1.0"
    entry_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    execution_mode: Literal["test-double", "live-model"] = "test-double"
    parser_backend: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=100)
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    duration_ms: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    cache_hit: bool
    failed_pages: list[int] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)

    @field_validator("parser_backend")
    @classmethod
    def backend_must_be_a_machine_token(cls, value: str) -> str:
        if _NAME_TOKEN.fullmatch(value) is None:
            raise ValueError("parser backend is invalid")
        return value

    @field_validator("parser_version")
    @classmethod
    def parser_version_must_be_a_machine_token(cls, value: str) -> str:
        if _VERSION_TOKEN.fullmatch(value) is None:
            raise ValueError("parser version is invalid")
        return value

    @field_validator("duration_ms")
    @classmethod
    def duration_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("duration must be finite")
        return value

    @field_validator("dependency_versions")
    @classmethod
    def dependency_versions_must_be_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64 or any(
            not name.strip()
            or len(name) > 100
            or not version.strip()
            or len(version) > 100
            or _NAME_TOKEN.fullmatch(name) is None
            or _VERSION_TOKEN.fullmatch(version) is None
            for name, version in value.items()
        ):
            raise ValueError("dependency versions are invalid")
        return value

    @field_validator("failed_pages")
    @classmethod
    def failed_pages_must_be_ordered(cls, value: list[int]) -> list[int]:
        if value != sorted(set(value)) or any(page < 1 for page in value):
            raise ValueError("failed pages must be ordered unique positive ordinals")
        return value

    @model_validator(mode="after")
    def page_ordinals_must_be_unique(self) -> Self:
        ordinals = [page.ordinal for page in self.pages]
        if ordinals != sorted(set(ordinals)):
            raise ValueError("observation pages must be ordered and unique")
        if set(ordinals) & set(self.failed_pages):
            raise ValueError("failed pages cannot also contain normalized output")
        return self


class _GoldDocument(BenchmarkContract):
    schema_version: Literal["1.0"] = "1.0"
    pages: list[OcrGoldPage]

    @model_validator(mode="after")
    def pages_must_be_ordered(self) -> Self:
        ordinals = [page.page_ordinal for page in self.pages]
        if ordinals != sorted(set(ordinals)):
            raise ValueError("gold pages must be ordered and unique")
        return self


class CaseQuality(BenchmarkContract):
    character_error_rate: float = Field(ge=0)
    reading_order_score: float = Field(ge=0, le=1)
    bbox_iou_mean: float = Field(ge=0, le=1)
    table_count_f1: float = Field(ge=0, le=1)
    formula_count_f1: float = Field(ge=0, le=1)


class BenchmarkCase(BenchmarkContract):
    case_key: str = Field(pattern=r"^[0-9a-f]{16}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    split: Literal["development", "validation", "test"]
    parser_backend: str
    parser_version: str
    dependency_versions: dict[str, str]
    duration_ms: float = Field(ge=0)
    peak_rss_bytes: int = Field(ge=0)
    cache_hit: bool
    normalized_page_count: int = Field(ge=0)
    failed_pages: list[int]
    quality: CaseQuality | None = None


class BenchmarkReport(BenchmarkContract):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: list[BenchmarkCase]
    resources: dict[str, int | float]
    quality_mean: dict[str, float]
    contains_raw_text: Literal[False] = False
    live_ocr_verified: bool = False


def run_benchmark(
    manifest_path: Path,
    observations_root: Path,
    *,
    generated_at: datetime | None = None,
) -> BenchmarkReport:
    """Read external artifacts serially and return a report with no source paths or text."""

    manifest_file = _absolute_regular_file(manifest_path, label="manifest")
    observations_dir = _external_results_directory(observations_root)
    manifest_bytes = manifest_file.read_bytes()
    manifest = OcrEvalManifest.model_validate_json(manifest_bytes)
    cases: list[BenchmarkCase] = []
    resources: list[ResourceObservation] = []
    qualities: list[CaseQuality] = []
    execution_modes: list[Literal["test-double", "live-model"]] = []
    for entry in manifest.entries:
        source = _absolute_regular_file(Path(entry.source_path), label="source")
        if source.stat().st_size != entry.size_bytes or _sha256(source) != entry.sha256:
            raise ValueError("manifest source hash or size does not match")
        observation_path = observations_dir / f"{entry.id}.json"
        observation = ExternalObservation.model_validate_json(
            _absolute_regular_file(observation_path, label="observation").read_bytes()
        )
        if observation.entry_id != entry.id:
            raise ValueError("observation entry id does not match manifest")
        execution_modes.append(observation.execution_mode)
        quality = None
        if entry.gold_path is not None:
            gold_path = _absolute_regular_file(Path(entry.gold_path), label="gold")
            quality = _evaluate_quality(
                _GoldDocument.model_validate_json(gold_path.read_bytes()),
                observation.pages,
            )
            qualities.append(quality)
        resources.append(
            ResourceObservation(
                duration_ms=observation.duration_ms,
                peak_rss_bytes=observation.peak_rss_bytes,
                cache_hit=observation.cache_hit,
            )
        )
        cases.append(
            BenchmarkCase(
                case_key=hashlib.sha256(entry.id.encode()).hexdigest()[:16],
                input_sha256=entry.sha256,
                split=entry.split,
                parser_backend=observation.parser_backend,
                parser_version=observation.parser_version,
                dependency_versions=observation.dependency_versions,
                duration_ms=observation.duration_ms,
                peak_rss_bytes=observation.peak_rss_bytes,
                cache_hit=observation.cache_hit,
                normalized_page_count=len(observation.pages),
                failed_pages=observation.failed_pages,
                quality=quality,
            )
        )
    if not resources:
        raise ValueError("benchmark manifest must contain at least one entry")
    quality_mean = (
        {
            field_name: mean(getattr(quality, field_name) for quality in qualities)
            for field_name in CaseQuality.model_fields
        }
        if qualities
        else {}
    )
    return BenchmarkReport(
        generated_at=generated_at or datetime.now(UTC),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        cases=cases,
        resources=summarize_resources(resources),
        quality_mean=quality_mean,
        live_ocr_verified=bool(execution_modes)
        and all(mode == "live-model" for mode in execution_modes),
    )


def write_report(report: BenchmarkReport, output: Path) -> Path:
    destination = output.expanduser().absolute()
    if _is_within(destination, _REPOSITORY_ROOT) and not any(
        _is_within(destination, allowed_root) for allowed_root in _RAW_LOCAL_ROOTS
    ):
        raise ValueError("benchmark reports inside the repository must use an ignored output root")
    if destination.is_symlink():
        raise ValueError("benchmark report output must not be a symlink")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    destination.chmod(0o600)
    return destination


def _evaluate_quality(gold: _GoldDocument, predictions: list[Page]) -> CaseQuality:
    predicted_by_ordinal = {page.ordinal: page for page in predictions}
    gold_text = "\n".join(page.text for page in gold.pages)
    predicted_text = "\n".join(
        "\n".join(
            block.text for block in sorted(page.blocks, key=lambda block: block.reading_order)
        )
        for page in predictions
    )
    expected_order: list[str] = []
    predicted_order: list[str] = []
    matched_ious: list[float] = []
    for gold_page in gold.pages:
        ordered_gold = sorted(gold_page.blocks, key=lambda block: block.reading_order)
        expected_order.extend(f"{gold_page.page_ordinal}:{block.id}" for block in ordered_gold)
        predicted_page = predicted_by_ordinal.get(gold_page.page_ordinal)
        if predicted_page is None:
            continue
        matches = _match_blocks(ordered_gold, predicted_page.blocks)
        predicted_order.extend(
            f"{gold_page.page_ordinal}:{gold_id}" if gold_id is not None else f"unmatched:{index}"
            for index, (gold_id, _) in enumerate(matches)
        )
        matched_ious.extend(iou for _, iou in matches if iou > 0)
    gold_table_count = _gold_kind_count(gold.pages, "table")
    gold_formula_count = _gold_kind_count(gold.pages, "formula")
    predicted_table_count = sum(
        block.type is BlockType.TABLE for page in predictions for block in page.blocks
    )
    predicted_formula_count = sum(
        block.type is BlockType.FORMULA for page in predictions for block in page.blocks
    )
    return CaseQuality(
        character_error_rate=character_error_rate(gold_text, predicted_text),
        reading_order_score=reading_order_score(expected_order, predicted_order),
        bbox_iou_mean=mean(matched_ious) if matched_ious else 0.0,
        table_count_f1=_count_f1(gold_table_count, predicted_table_count),
        formula_count_f1=_count_f1(gold_formula_count, predicted_formula_count),
    )


def _match_blocks(
    gold_blocks: list[OcrGoldBlock],
    predicted_blocks: list[Block],
) -> list[tuple[str | None, float]]:
    unmatched = set(range(len(gold_blocks)))
    matches: list[tuple[str | None, float]] = []
    for predicted in sorted(predicted_blocks, key=lambda block: block.reading_order):
        predicted_box = (
            predicted.bbox_norm.x,
            predicted.bbox_norm.y,
            predicted.bbox_norm.width,
            predicted.bbox_norm.height,
        )
        candidates = [
            (
                bbox_iou(
                    (
                        gold_blocks[index].bbox_norm.x,
                        gold_blocks[index].bbox_norm.y,
                        gold_blocks[index].bbox_norm.width,
                        gold_blocks[index].bbox_norm.height,
                    ),
                    predicted_box,
                ),
                index,
            )
            for index in unmatched
        ]
        if not candidates:
            matches.append((None, 0.0))
            continue
        best_iou, best_index = max(candidates)
        if best_iou <= 0:
            matches.append((None, 0.0))
            continue
        unmatched.remove(best_index)
        matches.append((gold_blocks[best_index].id, best_iou))
    return matches


def _gold_kind_count(pages: list[OcrGoldPage], kind: Literal["table", "formula"]) -> int:
    block_count = sum(block.kind == kind for page in pages for block in page.blocks)
    if kind == "table":
        return max(block_count, sum(bool(page.table_cells) for page in pages))
    return max(block_count, sum(len(page.formulas) for page in pages))


def _count_f1(expected: int, predicted: int) -> float:
    if expected == 0 and predicted == 0:
        return 1.0
    return 2 * min(expected, predicted) / (expected + predicted)


def _external_results_directory(path: Path) -> Path:
    root = path.expanduser()
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("observations root must be an absolute non-symlink directory")
    resolved = root.resolve(strict=True)
    if _is_within(resolved, _REPOSITORY_ROOT) and not any(
        _is_within(resolved, allowed_root) for allowed_root in _RAW_LOCAL_ROOTS
    ):
        raise ValueError("raw observations inside the repository must use an ignored root")
    return resolved


def _absolute_regular_file(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute() or expanded.is_symlink() or not expanded.is_file():
        raise ValueError(f"{label} must be an absolute non-symlink file")
    return expanded.resolve(strict=True)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate external OCR benchmark observations.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--observations-root", required=True, type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/reports/generated/ocr-benchmark.json"),
    )
    arguments = parser.parse_args()
    report = run_benchmark(arguments.manifest, arguments.observations_root)
    output = write_report(report, arguments.output)
    print(f"wrote metrics-only report to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
