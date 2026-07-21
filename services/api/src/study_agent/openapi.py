from typing import Any

from study_agent.main import create_app


def build_openapi_document() -> dict[str, Any]:
    return create_app().openapi()
