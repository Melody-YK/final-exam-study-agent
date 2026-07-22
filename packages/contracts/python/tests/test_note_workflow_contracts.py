from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from study_contracts import (
    CoverageUnitSnapshot,
    CoverageUnitStatus,
    CreateNoteBatchRequest,
    EtaRange,
    MergedNoteBatchRequest,
    NoteAstNode,
    NoteBatchCommandKind,
    NoteBatchMode,
    NoteBatchSnapshot,
    NoteContentAstV1,
    NoteExportSnapshot,
    NoteItemSnapshot,
    NoteVersionCoverage,
    PerDocumentNoteBatchRequest,
    StructuredNoteDraftV1,
)

NOW = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)


def _item_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "item-1",
        "input_ids": ["input-1"],
        "status": "queued",
        "phase": None,
        "elapsed_seconds": 0,
        "eta": None,
        "eta_unavailable_reason": "not_started",
        "attempt": 0,
        "note_id": None,
        "failure_code": None,
        "retryable_in_new_batch": False,
    }
    payload.update(overrides)
    return payload


def _batch_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "batch-1",
        "retry_of_batch_id": None,
        "course_id": "course-1",
        "mode": "merged",
        "status": "running",
        "completed_items": 0,
        "total_items": 1,
        "inputs": [],
        "coverage_units": [],
        "items": [_item_payload()],
        "last_event_sequence": 0,
        "created_at": NOW,
        "started_at": NOW,
        "completed_at": None,
    }
    payload.update(overrides)
    return payload


def _coverage_unit(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": "unit-1",
        "input_id": "input-1",
        "ordinal": 1,
        "unit_type": "slide",
        "locator": "slide:1",
        "status": "covered",
        "reason_code": None,
    }
    payload.update(overrides)
    return payload


def _draft_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Process management",
        "body_markdown": "# Process management\n\nA process owns resources.",
        "claims": [
            {
                "id": "claim-1",
                "text": "A process owns resources.",
                "citation_ids": ["citation-1"],
            }
        ],
        "citations": [
            {
                "id": "citation-1",
                "evidence_id": "evidence-1",
                "coverage_unit_ids": ["unit-1"],
            }
        ],
        "coverage_unit_refs": ["unit-1"],
        "content_ast": {
            "nodes": [
                {
                    "id": "node-1",
                    "type": "paragraph",
                    "children": [
                        {
                            "id": "node-2",
                            "type": "citation",
                            "citation_id": "citation-1",
                        }
                    ],
                }
            ]
        },
    }
    payload.update(overrides)
    return payload


def test_create_request_is_discriminated_and_normalizes_optional_text() -> None:
    adapter = TypeAdapter(CreateNoteBatchRequest)

    merged = adapter.validate_python(
        {
            "mode": "merged",
            "document_ids": ["doc-1", "doc-2"],
            "title": "  Final review  ",
            "section_path": ["  Finals  ", "Operating systems"],
        }
    )
    split = adapter.validate_python(
        {
            "mode": "per_document",
            "document_ids": ["doc-1"],
            "title_prefix": "   ",
            "section_path": None,
        }
    )

    assert isinstance(merged, MergedNoteBatchRequest)
    assert merged.mode is NoteBatchMode.MERGED
    assert merged.title == "Final review"
    assert merged.section_path == ["Finals", "Operating systems"]
    assert isinstance(split, PerDocumentNoteBatchRequest)
    assert split.title_prefix is None
    assert split.section_path is None


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "merged", "document_ids": ["doc-1", "doc-1"]},
        {"mode": "per_document", "document_ids": []},
        {"mode": "merged", "document_ids": ["doc-1"], "title_prefix": "wrong field"},
        {"mode": "per_document", "document_ids": ["doc-1"], "title": "wrong field"},
        {"mode": "merged", "document_ids": ["   "]},
        {"mode": "merged", "document_ids": ["doc-1"], "title": "x" * 256},
        {"mode": "per_document", "document_ids": ["doc-1"], "title_prefix": "x" * 256},
        {"mode": "merged", "document_ids": ["doc-1"], "section_path": ["unit"] * 33},
        {"schema_version": "2.0", "mode": "merged", "document_ids": ["doc-1"]},
        {"mode": "unsupported", "document_ids": ["doc-1"]},
    ],
)
def test_create_request_rejects_duplicates_empty_selection_and_wrong_mode_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(CreateNoteBatchRequest).validate_python(payload)


def test_eta_requires_timezone_and_ordered_range() -> None:
    eta = EtaRange(lower_seconds=30, upper_seconds=90, confidence="low", as_of=NOW)
    assert eta.lower_seconds == 30

    with pytest.raises(ValidationError, match="timezone"):
        EtaRange(
            lower_seconds=30,
            upper_seconds=90,
            confidence="low",
            as_of=datetime(2026, 7, 22, 8, 0),
        )
    with pytest.raises(ValidationError, match="must not exceed"):
        EtaRange(lower_seconds=90, upper_seconds=30, confidence="low", as_of=NOW)


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        ("skipped", None),
        ("failed", None),
        ("pending", "not_started"),
        ("covered", "covered_by_provider"),
    ],
)
def test_coverage_reason_must_match_status(status: str, reason_code: str | None) -> None:
    with pytest.raises(ValidationError):
        CoverageUnitSnapshot.model_validate(_coverage_unit(status=status, reason_code=reason_code))

    skipped = CoverageUnitSnapshot.model_validate(
        _coverage_unit(status="skipped", reason_code="blank_unit")
    )
    assert skipped.status is CoverageUnitStatus.SKIPPED


@pytest.mark.parametrize(
    "overrides",
    [
        {"eta": None, "eta_unavailable_reason": None},
        {
            "eta": {
                "lower_seconds": 1,
                "upper_seconds": 2,
                "confidence": "low",
                "as_of": NOW,
            },
            "eta_unavailable_reason": "retrying",
        },
        {
            "status": "succeeded",
            "eta": {
                "lower_seconds": 1,
                "upper_seconds": 2,
                "confidence": "low",
                "as_of": NOW,
            },
            "eta_unavailable_reason": None,
        },
        {"status": "succeeded", "eta": None, "eta_unavailable_reason": "not_started"},
        {"status": "running", "eta": None, "eta_unavailable_reason": "terminal"},
        {"input_ids": ["input-1", "input-1"]},
    ],
)
def test_item_eta_and_input_invariants_reject_invalid_snapshots(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        NoteItemSnapshot.model_validate(_item_payload(**overrides))

    terminal = NoteItemSnapshot.model_validate(
        _item_payload(status="succeeded", eta_unavailable_reason="terminal")
    )
    assert terminal.eta_unavailable_reason == "terminal"


def test_batch_defaults_are_stable() -> None:
    snapshot = NoteBatchSnapshot.model_validate(_batch_payload())

    assert snapshot.command_kind is NoteBatchCommandKind.CREATE
    assert snapshot.section_path == ["未分类"]
    assert snapshot.completed_items == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"completed_items": 2, "total_items": 1},
        {"status": "succeeded", "completed_at": None},
        {"status": "running", "completed_at": NOW},
        {"created_at": datetime(2026, 7, 22, 8, 0)},
        {"started_at": datetime(2026, 7, 22, 8, 0)},
        {
            "status": "succeeded",
            "completed_at": datetime(2026, 7, 22, 8, 0),
        },
        {"mode": "merged", "title_prefix": "invalid"},
        {"mode": "per_document", "title": "invalid"},
    ],
)
def test_batch_count_timestamp_and_mode_invariants(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NoteBatchSnapshot.model_validate(_batch_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"command_kind": "retry_failed", "retry_of_batch_id": None},
        {"command_kind": "create", "retry_of_batch_id": "batch-parent"},
        {"command_kind": "regeneration"},
        {
            "command_kind": "regeneration",
            "mode": "per_document",
            "target_note_id": "note-1",
            "target_note_version": 1,
            "target_note_version_sha256": "a" * 64,
        },
        {"command_kind": "create", "target_note_id": "note-1"},
    ],
)
def test_batch_command_targets_fail_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NoteBatchSnapshot.model_validate(_batch_payload(**overrides))


@pytest.mark.parametrize(
    "missing_field",
    ["target_note_id", "target_note_version", "target_note_version_sha256"],
)
def test_regeneration_requires_each_exact_version_target_field(missing_field: str) -> None:
    target: dict[str, object] = {
        "command_kind": "regeneration",
        "target_note_id": "note-1",
        "target_note_version": 2,
        "target_note_version_sha256": "a" * 64,
    }
    target.pop(missing_field)

    with pytest.raises(ValidationError, match="exact merged Note target"):
        NoteBatchSnapshot.model_validate(_batch_payload(**target))


def test_retry_and_regeneration_commands_accept_complete_targets() -> None:
    retries = [
        NoteBatchSnapshot.model_validate(
            _batch_payload(command_kind=kind, retry_of_batch_id="batch-parent")
        )
        for kind in ("retry_failed", "retry_gaps")
    ]
    regeneration = NoteBatchSnapshot.model_validate(
        _batch_payload(
            command_kind="regeneration",
            target_note_id="note-1",
            target_note_version=2,
            target_note_version_sha256="a" * 64,
        )
    )

    assert {retry.command_kind for retry in retries} == {
        NoteBatchCommandKind.RETRY_FAILED,
        NoteBatchCommandKind.RETRY_GAPS,
    }
    assert all(retry.retry_of_batch_id == "batch-parent" for retry in retries)
    assert regeneration.target_note_version == 2


def test_batch_mode_accepts_its_matching_title_field() -> None:
    merged = NoteBatchSnapshot.model_validate(_batch_payload(title="Final review"))
    per_document = NoteBatchSnapshot.model_validate(
        _batch_payload(mode="per_document", title_prefix="Review")
    )

    assert merged.title == "Final review"
    assert per_document.title_prefix == "Review"


def test_draft_ast_and_coverage_contracts_accept_closed_references() -> None:
    draft = StructuredNoteDraftV1.model_validate(_draft_payload())
    coverage = NoteVersionCoverage(
        policy_version="coverage-v1",
        status="partial",
        basis="generated",
        generated_from_version=None,
        manifest_sha256="a" * 64,
        units=[CoverageUnitSnapshot.model_validate(_coverage_unit())],
    )
    edited_coverage = NoteVersionCoverage(
        policy_version="coverage-v1",
        status="partial",
        basis="user_edited_from_generated_version",
        generated_from_version=2,
        manifest_sha256="a" * 64,
        units=[CoverageUnitSnapshot.model_validate(_coverage_unit())],
    )

    assert draft.schema_version == "1.0"
    assert coverage.units[0].id == "unit-1"
    assert edited_coverage.generated_from_version == 2


def test_ast_rejects_duplicate_ids_and_missing_type_attributes() -> None:
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        NoteContentAstV1.model_validate(
            {"nodes": [{"id": "node-1", "type": "text"}, {"id": "node-1", "type": "text"}]}
        )
    with pytest.raises(ValidationError, match="identifiers must be unique"):
        NoteContentAstV1.model_validate(
            {
                "nodes": [
                    {
                        "id": "node-1",
                        "type": "paragraph",
                        "children": [{"id": "node-1", "type": "text"}],
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="heading nodes require level"):
        NoteAstNode.model_validate({"id": "node-1", "type": "heading"})
    with pytest.raises(ValidationError, match="citation nodes require citation_id"):
        NoteAstNode.model_validate({"id": "node-1", "type": "citation"})


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "claims": [
                {"id": "claim-1", "text": "one", "citation_ids": ["citation-1"]},
                {"id": "claim-1", "text": "two", "citation_ids": ["citation-1"]},
            ]
        },
        {
            "citations": [
                {
                    "id": "citation-1",
                    "evidence_id": "evidence-1",
                    "coverage_unit_ids": ["unit-1"],
                },
                {
                    "id": "citation-1",
                    "evidence_id": "evidence-2",
                    "coverage_unit_ids": ["unit-1"],
                },
            ]
        },
        {"coverage_unit_refs": ["unit-1", "unit-1"]},
        {
            "claims": [
                {
                    "id": "claim-1",
                    "text": "one",
                    "citation_ids": ["citation-1", "citation-1"],
                }
            ]
        },
        {
            "citations": [
                {
                    "id": "citation-1",
                    "evidence_id": "evidence-1",
                    "coverage_unit_ids": ["unit-1", "unit-1"],
                }
            ]
        },
        {"claims": [{"id": "claim-1", "text": "one", "citation_ids": ["citation-missing"]}]},
        {
            "citations": [
                {
                    "id": "citation-1",
                    "evidence_id": "evidence-1",
                    "coverage_unit_ids": ["unit-missing"],
                }
            ]
        },
        {
            "content_ast": {
                "nodes": [
                    {
                        "id": "node-parent",
                        "type": "paragraph",
                        "children": [
                            {
                                "id": "node-citation",
                                "type": "citation",
                                "citation_id": "citation-missing",
                            }
                        ],
                    }
                ]
            }
        },
    ],
)
def test_draft_references_are_closed_and_unique(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        StructuredNoteDraftV1.model_validate(_draft_payload(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {"basis": "user_edited_from_generated_version", "generated_from_version": None},
        {"basis": "generated", "generated_from_version": 1},
        {"basis": "legacy_backfill", "generated_from_version": 1},
        {"units": [_coverage_unit(), _coverage_unit()]},
    ],
)
def test_version_coverage_basis_and_units_are_consistent(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "policy_version": "coverage-v1",
        "status": "partial",
        "basis": "generated",
        "generated_from_version": None,
        "manifest_sha256": "a" * 64,
        "units": [_coverage_unit()],
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        NoteVersionCoverage.model_validate(payload)


def test_export_expiry_must_include_timezone() -> None:
    payload: dict[str, object] = {
        "id": "export-1",
        "note_id": "note-1",
        "note_version": 1,
        "status": "available",
        "version_preview_path": "/api/v1/notes/note-1/versions/1",
    }
    with pytest.raises(ValidationError, match="timezone"):
        NoteExportSnapshot.model_validate({**payload, "expires_at": datetime(2026, 7, 23, 8, 0)})

    snapshot = NoteExportSnapshot.model_validate({**payload, "expires_at": NOW})
    assert snapshot.expires_at == NOW
