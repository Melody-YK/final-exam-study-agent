from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from study_agent.providers.protocols import VisionImage, VisionJsonCompletionPrompt
from study_agent.providers.vision import OpenAICompatibleVisionProvider

from ..fakes.provider_server import ScriptedProviderServer, ScriptedResponse


@pytest.mark.asyncio
async def test_vision_json_completion_sends_page_image_and_normalizes_result() -> None:
    response_payload = {
        "extracted_text": "题目：计算页面号。",  # noqa: RUF001
        "question_type": "calculation",
        "conditions": ["页面大小为 100 字节"],
        "reference_answer": "页号为 2。",
        "uncertain_spans": [],
        "evidence_complete": True,
        "confidence": "high",
        "reason": "题干和答案均清晰。",
    }
    server = ScriptedProviderServer(
        ScriptedResponse(
            json_body={
                "id": "vision-review-1",
                "model": "vision-contract-model",
                "choices": [{"message": {"content": json.dumps(response_payload)}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
            }
        )
    )
    client = httpx.AsyncClient(transport=server.transport)
    provider = OpenAICompatibleVisionProvider(
        api_key=SecretStr("vision-contract-secret"),
        base_url="http://vision.test/v1",
        model="vision-contract-model",
        timeout_seconds=0.1,
        max_attempts=1,
        http_client=client,
    )
    try:
        result = await provider.complete_json(
            VisionJsonCompletionPrompt(
                system_prompt="Return JSON only.",
                payload={"parsed_text_hint": "<table>broken</table>"},
                images=(VisionImage(data=b"page-image", media_type="image/png"),),
            )
        )
    finally:
        await client.aclose()

    assert result.payload == response_payload
    assert result.model == "vision-contract-model"
    assert result.provider_response_id == "vision-review-1"
    assert result.usage == {"input_tokens": 20, "output_tokens": 12, "total_tokens": 32}
    request = server.requests[0]
    assert request.path == "/v1/chat/completions"
    assert request.json_body["response_format"] == {"type": "json_object"}
    user_content = request.json_body["messages"][1]["content"]
    assert json.loads(user_content[0]["text"])["request"]["parsed_text_hint"] == (
        "<table>broken</table>"
    )
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
