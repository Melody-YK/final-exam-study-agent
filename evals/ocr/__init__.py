"""OCR evaluation contracts and deterministic metrics."""

from evals.ocr.manifest import OcrEvalEntry, OcrEvalManifest, OcrGoldPage
from evals.ocr.metrics import (
    ResourceObservation,
    bbox_iou,
    character_error_rate,
    reading_order_score,
    summarize_resources,
)

__all__ = [
    "OcrEvalEntry",
    "OcrEvalManifest",
    "OcrGoldPage",
    "ResourceObservation",
    "bbox_iou",
    "character_error_rate",
    "reading_order_score",
    "summarize_resources",
]
