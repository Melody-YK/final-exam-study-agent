import json
from pathlib import Path

from study_agent.openapi import build_openapi_document

FORBIDDEN_PUBLIC_SCHEMA_PREFIXES = (
    "NoteAst",
    "NoteContentAst",
    "NoteExport",
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


def test_query_conversation_and_note_batch_contracts_are_present() -> None:
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

    create_batch = paths["/api/v1/courses/{course_id}/note-batches"]["post"]
    assert create_batch["responses"]["202"]["description"]
    assert "Idempotency-Key" in {
        parameter["name"] for parameter in create_batch["parameters"] if parameter["in"] == "header"
    }
    request_schema = create_batch["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/MergedNoteBatchRequest")
    assert "PerDocumentNoteBatchRequest" not in schemas
    assert "NoteBatchSnapshot" not in schemas

    get_batch = paths["/api/v1/note-batches/{batch_id}"]["get"]
    regenerate_batch = paths["/api/v1/notes/{note_id}/regeneration-batches"]["post"]
    regeneration_headers = {
        parameter["name"]
        for parameter in regenerate_batch["parameters"]
        if parameter["in"] == "header"
    }
    assert {"Idempotency-Key", "If-Match"} <= regeneration_headers
    assert "requestBody" not in regenerate_batch
    create_response = create_batch["responses"]["202"]["content"]["application/json"]["schema"]
    regeneration_response = regenerate_batch["responses"]["202"]["content"]["application/json"][
        "schema"
    ]
    get_response = get_batch["responses"]["200"]["content"]["application/json"]["schema"]
    assert (
        create_response
        == regeneration_response
        == get_response
        == {"$ref": "#/components/schemas/LocalDemoNoteBatchSnapshot"}
    )

    demo_snapshot = schemas["LocalDemoNoteBatchSnapshot"]
    assert demo_snapshot["properties"]["mode"]["const"] == "merged"
    assert demo_snapshot["properties"]["command_kind"]["enum"] == ["create", "regeneration"]
    assert schemas["NoteBatchStatus"]["enum"] == [
        "queued",
        "running",
        "partial_success",
        "succeeded",
        "failed",
        "cancelling",
        "cancelled",
    ]
    assert schemas["NoteGenerationPhase"]["enum"] == [
        "validating_inputs",
        "segmenting",
        "retrieving",
        "outlining",
        "generating",
        "validating_output",
        "saving",
    ]
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
    assert "/api/v1/courses/{course_id}/note-batches" in paths
    assert "/api/v1/note-batches/{batch_id}" in paths

    _assert_dormant_note_workflow_schemas_are_absent(schemas)


def test_committed_openapi_matches_generated_document() -> None:
    root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (root / "packages/contracts/openapi/openapi.json").read_text(encoding="utf-8")
    )

    assert committed == build_openapi_document()
