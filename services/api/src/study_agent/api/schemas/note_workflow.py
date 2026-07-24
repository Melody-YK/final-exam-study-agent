"""HTTP schema facade for the versioned note-workflow contracts."""

from typing import Literal

from study_contracts import (
    CoverageUnitSnapshot,
    CreateNoteBatchRequest,
    EtaRange,
    MergedNoteBatchRequest,
    NoteBatchCommandKind,
    NoteBatchMode,
    NoteBatchSnapshot,
    NoteBatchStyle,
    NoteContentAstV1,
    NoteExportSnapshot,
    NoteItemSnapshot,
    NoteVersionCoverage,
    PerDocumentNoteBatchRequest,
    StructuredNoteDraftV1,
)


class LocalDemoNoteBatchSnapshot(NoteBatchSnapshot):
    """Public snapshot shape supported by the local merged/create demo route."""

    command_kind: Literal[NoteBatchCommandKind.CREATE] = NoteBatchCommandKind.CREATE
    mode: Literal[NoteBatchMode.MERGED] = NoteBatchMode.MERGED


__all__ = [
    "CoverageUnitSnapshot",
    "CreateNoteBatchRequest",
    "EtaRange",
    "LocalDemoNoteBatchSnapshot",
    "MergedNoteBatchRequest",
    "NoteBatchCommandKind",
    "NoteBatchStyle",
    "NoteContentAstV1",
    "NoteExportSnapshot",
    "NoteItemSnapshot",
    "NoteVersionCoverage",
    "PerDocumentNoteBatchRequest",
    "StructuredNoteDraftV1",
]
