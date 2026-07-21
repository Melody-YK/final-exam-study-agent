"""Parser interfaces shared by the worker router and adapter implementations."""

from study_worker.parsers.complex import (
    BackendAvailability,
    ComplexityRouter,
    OcrBackend,
    PageComplexity,
    RouteDecision,
)
from study_worker.parsers.paddle_general import (
    PaddleGeneralOutput,
    normalize_paddle_general_output,
    polygon_to_bbox,
)
from study_worker.parsers.pp_structure import PPStructureOutput, normalize_pp_structure_output
from study_worker.parsers.protocols import (
    Clock,
    Parser,
    ParserCapability,
    ParseRequest,
    ParserResult,
)

__all__ = [
    "BackendAvailability",
    "Clock",
    "ComplexityRouter",
    "OcrBackend",
    "PPStructureOutput",
    "PaddleGeneralOutput",
    "PageComplexity",
    "ParseRequest",
    "Parser",
    "ParserCapability",
    "ParserResult",
    "RouteDecision",
    "normalize_paddle_general_output",
    "normalize_pp_structure_output",
    "polygon_to_bbox",
]
