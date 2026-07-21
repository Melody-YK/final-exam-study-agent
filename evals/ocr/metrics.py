"""Dependency-light OCR quality and resource metrics."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from statistics import mean


def character_error_rate(reference: str, prediction: str) -> float:
    """Return Unicode NFC character edit distance divided by reference length."""

    expected = unicodedata.normalize("NFC", reference)
    actual = unicodedata.normalize("NFC", prediction)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_character in enumerate(expected, start=1):
        current = [row]
        for column, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (expected_character != actual_character),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def reading_order_score(expected_ids: list[str], predicted_ids: list[str]) -> float:
    """Measure ordered coverage as LCS length over the expected sequence."""

    if len(expected_ids) != len(set(expected_ids)) or len(predicted_ids) != len(set(predicted_ids)):
        raise ValueError("reading-order identifiers must be unique")
    if not expected_ids:
        return 1.0 if not predicted_ids else 0.0
    previous = [0] * (len(predicted_ids) + 1)
    for expected in expected_ids:
        current = [0]
        for column, predicted in enumerate(predicted_ids, start=1):
            current.append(
                previous[column - 1] + 1
                if expected == predicted
                else max(previous[column], current[-1])
            )
        previous = current
    return previous[-1] / len(expected_ids)


def bbox_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Return intersection-over-union for normalized ``x, y, width, height`` boxes."""

    _validate_box(first)
    _validate_box(second)
    first_x2, first_y2 = first[0] + first[2], first[1] + first[3]
    second_x2, second_y2 = second[0] + second[2], second[1] + second[3]
    intersection_width = max(0.0, min(first_x2, second_x2) - max(first[0], second[0]))
    intersection_height = max(0.0, min(first_y2, second_y2) - max(first[1], second[1]))
    intersection = intersection_width * intersection_height
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    duration_ms: float
    peak_rss_bytes: int
    cache_hit: bool

    def __post_init__(self) -> None:
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("duration_ms must be finite and non-negative")
        if self.peak_rss_bytes < 0:
            raise ValueError("peak_rss_bytes must be non-negative")


def summarize_resources(observations: list[ResourceObservation]) -> dict[str, int | float]:
    if not observations:
        raise ValueError("at least one resource observation is required")
    durations = sorted(observation.duration_ms for observation in observations)
    return {
        "count": len(observations),
        "duration_mean_ms": mean(durations),
        "duration_p50_ms": _nearest_rank(durations, 0.50),
        "duration_p95_ms": _nearest_rank(durations, 0.95),
        "peak_rss_bytes": max(observation.peak_rss_bytes for observation in observations),
        "cache_hit_rate": sum(observation.cache_hit for observation in observations)
        / len(observations),
    }


def _validate_box(box: tuple[float, float, float, float]) -> None:
    x, y, width, height = box
    if not all(math.isfinite(value) for value in box):
        raise ValueError("bbox values must be finite")
    if x < 0 or y < 0 or width < 0 or height < 0 or x + width > 1 or y + height > 1:
        raise ValueError("bbox must fit normalized page bounds")


def _nearest_rank(values: list[float], percentile: float) -> float:
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]
