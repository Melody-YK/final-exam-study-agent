import json
from pathlib import Path

from study_agent.openapi import build_openapi_document

FORBIDDEN_PUBLIC_SCHEMA_PREFIXES = (
    "CoverageUnit",
    "EtaConfidence",
    "EtaRange",
    "EtaUnavailableReason",
    "MergedNoteBatch",
    "NoteAst",
    "NoteBatch",
    "NoteContentAst",
    "NoteCoverage",
    "NoteExport",
    "NoteGeneration",
    "NoteInput",
    "NoteItem",
    "NoteSourceOverlay",
    "NoteVersion",
    "PerDocumentNoteBatch",
    "StructuredNoteDraft",
)


def _assert_dormant_note_workflow_schemas_are_absent(schemas: dict[str, object]) -> None:
    leaked = sorted(name for name in schemas if name.startswith(FORBIDDEN_PUBLIC_SCHEMA_PREFIXES))
    assert leaked == []


def test_openapi_document_is_stable_and_contains_health_contract() -> None:
    document = build_openapi_document()

    assert document["info"]["version"] == "0.1.0"
    assert "/healthz" in document["paths"]
    assert "HealthResponse" in document["components"]["schemas"]
    assert "api_key" not in json.dumps(document).lower()


def test_query_conversation_contract_is_present_without_note_workflow_routes() -> None:
    document = build_openapi_document()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    conversation_path = paths["/api/v1/courses/{course_id}/conversations"]
    assert {"get", "post"} <= conversation_path.keys()
    assert "get" in paths["/api/v1/conversations/{conversation_id}/queries"]
    assert {"ConversationCreate", "ConversationResponse"} <= schemas.keys()

    query_create = schemas["QueryCreate"]
    assert "conversation_id" in query_create["properties"]
    assert "conversation_id" not in query_create.get("required", [])
    assert "conversation_id" in schemas["QueryResponse"]["required"]

    excluded_route_fragment = "-".join(("note", "batches"))
    assert all(excluded_route_fragment not in path for path in paths)
    _assert_dormant_note_workflow_schemas_are_absent(schemas)


def test_note_workflow_capability_preserves_legacy_notes_and_hides_dormant_contracts() -> None:
    document = build_openapi_document()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    runtime_capabilities = schemas["RuntimeCapabilitiesResponse"]
    assert "note_workflow" in runtime_capabilities["required"]
    assert runtime_capabilities["properties"]["note_workflow"]["$ref"].endswith(
        "/NoteWorkflowCapabilityResponse"
    )

    note_workflow = schemas["NoteWorkflowCapabilityResponse"]
    expected_fields = {"enabled", "generation", "export", "eta"}
    assert set(note_workflow["properties"]) == expected_fields
    assert set(note_workflow["required"]) == expected_fields

    legacy_create = paths["/api/v1/courses/{course_id}/notes"]["post"]
    response_schema = legacy_create["responses"]["201"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/NoteResponse")
    assert "/api/v1/notes/{note_id}/versions/{version}" not in paths
    assert all("note-batches" not in path for path in paths)

    _assert_dormant_note_workflow_schemas_are_absent(schemas)


def test_committed_openapi_matches_generated_document() -> None:
    root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (root / "packages/contracts/openapi/openapi.json").read_text(encoding="utf-8")
    )

    assert committed == build_openapi_document()
