import json
from pathlib import Path

from study_agent.openapi import build_openapi_document


def test_openapi_document_is_stable_and_contains_health_contract() -> None:
    document = build_openapi_document()

    assert document["info"]["version"] == "0.1.0"
    assert "/healthz" in document["paths"]
    assert "HealthResponse" in document["components"]["schemas"]
    assert "api_key" not in json.dumps(document).lower()


def test_committed_openapi_matches_generated_document() -> None:
    root = Path(__file__).resolve().parents[4]
    committed = json.loads(
        (root / "packages/contracts/openapi/openapi.json").read_text(encoding="utf-8")
    )

    assert committed == build_openapi_document()
