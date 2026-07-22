import json
from pathlib import Path

from study_agent.openapi import build_openapi_document


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
    excluded_prefixes = tuple(
        f"Note{suffix}" for suffix in ("Batch", "Export", "Generation", "Version")
    )
    assert not any(name.startswith(excluded_prefixes) for name in schemas)


def test_committed_openapi_matches_generated_document() -> None:
    root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (root / "packages/contracts/openapi/openapi.json").read_text(encoding="utf-8")
    )

    assert committed == build_openapi_document()
