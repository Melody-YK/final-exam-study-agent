"""Disabled-by-default routing between General OCR and experimental complex parsing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

COMPLEX_PARSER_ENABLED_DEFAULT = False


class OcrBackend(StrEnum):
    PADDLE_GENERAL = "paddle-general"
    PP_STRUCTURE_V3 = "pp-structure-v3"
    MINERU = "mineru"
    PAID_OCR = "paid-ocr"


@dataclass(frozen=True, slots=True)
class PageComplexity:
    table_regions: int = 0
    formula_regions: int = 0
    estimated_columns: int = 1
    overlapping_regions: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.table_regions,
                self.formula_regions,
                self.estimated_columns,
                self.overlapping_regions,
            )
            < 0
        ):
            raise ValueError("page complexity counts must not be negative")
        if self.estimated_columns < 1:
            raise ValueError("estimated_columns must be positive")

    @property
    def requires_complex_parser(self) -> bool:
        return (
            self.table_regions > 0
            or self.formula_regions > 0
            or self.estimated_columns > 1
            or self.overlapping_regions > 0
        )


@dataclass(frozen=True, slots=True)
class RouteDecision:
    backend: OcrBackend
    reason_code: str
    experimental: bool = False


@dataclass(frozen=True, slots=True)
class BackendAvailability:
    backend: OcrBackend
    available: bool
    reason_code: str | None


class ComplexityRouter:
    def __init__(
        self,
        *,
        enabled: bool = COMPLEX_PARSER_ENABLED_DEFAULT,
        pp_structure_available: bool = False,
    ) -> None:
        self._enabled = enabled
        self._pp_structure_available = pp_structure_available

    def route(self, complexity: PageComplexity) -> RouteDecision:
        if not complexity.requires_complex_parser:
            return RouteDecision(OcrBackend.PADDLE_GENERAL, "GENERAL_PAGE")
        if not self._enabled:
            return RouteDecision(OcrBackend.PADDLE_GENERAL, "COMPLEX_PARSER_DISABLED")
        if not self._pp_structure_available:
            return RouteDecision(OcrBackend.PADDLE_GENERAL, "PP_STRUCTURE_UNAVAILABLE")
        return RouteDecision(
            OcrBackend.PP_STRUCTURE_V3,
            "COMPLEX_PAGE_EXPERIMENT",
            experimental=True,
        )

    def availability(self, backend: OcrBackend) -> BackendAvailability:
        if backend is OcrBackend.PADDLE_GENERAL:
            return BackendAvailability(backend, True, None)
        if backend is OcrBackend.PP_STRUCTURE_V3:
            available = self._enabled and self._pp_structure_available
            reason = (
                None
                if available
                else (
                    "COMPLEX_PARSER_DISABLED" if not self._enabled else "PP_STRUCTURE_UNAVAILABLE"
                )
            )
            return BackendAvailability(backend, available, reason)
        if backend is OcrBackend.MINERU:
            return BackendAvailability(backend, False, "MINERU_DISABLED")
        return BackendAvailability(backend, False, "PAID_OCR_DISABLED")
