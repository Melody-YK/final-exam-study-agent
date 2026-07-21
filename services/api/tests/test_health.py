from httpx import ASGITransport, AsyncClient

from study_agent.main import app


async def test_health_reports_service_identity() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "study-agent-api",
        "version": "0.1.0",
    }
    assert len(response.headers["x-trace-id"]) == 32
