from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from study_agent.config import AppMode, Settings
from study_agent.main import create_app, run
from study_agent.storage.local import LocalStorage


def _local_app(tmp_path: Path, **overrides: object):  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.LOCAL,
        local_storage_root=tmp_path / "storage",
        **overrides,
    )
    return create_app(settings=settings, storage=LocalStorage(tmp_path / "storage"))


@pytest.mark.asyncio
async def test_local_api_rejects_dns_rebinding_cross_origin_and_forwarded_headers(
    tmp_path: Path,
) -> None:
    app = _local_app(tmp_path)
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        rebound = await client.get("/healthz", headers={"Host": "attacker.example"})
        cross_origin = await client.post(
            "/api/v1/courses",
            headers={"Origin": "https://attacker.example"},
            json={"title": "blocked"},
        )
        forwarded = await client.get(
            "/healthz",
            headers={"X-Forwarded-Host": "attacker.example"},
        )

    assert rebound.status_code == 400
    assert rebound.headers["X-Content-Type-Options"] == "nosniff"
    assert cross_origin.status_code == 403
    assert forwarded.status_code == 400


@pytest.mark.asyncio
async def test_api_adds_browser_security_headers(tmp_path: Path) -> None:
    app = _local_app(tmp_path)
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in response.headers


@pytest.mark.asyncio
async def test_local_api_accepts_bracketed_ipv6_loopback_host(tmp_path: Path) -> None:
    app = _local_app(tmp_path, bind_host="::1")
    transport = ASGITransport(app=app, client=("::1", 51000))
    async with AsyncClient(transport=transport, base_url="http://[::1]:8000") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hosts",
    [
        [("Host", "localhost:8000"), ("Host", "attacker.example")],
        [("Host", "attacker.example"), ("Host", "localhost:8000")],
    ],
)
async def test_local_api_rejects_duplicate_host_headers(
    tmp_path: Path,
    hosts: list[tuple[str, str]],
) -> None:
    app = _local_app(tmp_path)
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.get(
            "/healthz",
            headers=hosts,
        )

    assert response.status_code == 400
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "localhost:",
        "localhost:not-a-port",
        "user@localhost",
        "localhost/path",
        "::1",
        "[::1",
    ],
)
async def test_local_api_rejects_malformed_host_header(tmp_path: Path, host: str) -> None:
    app = _local_app(tmp_path)
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.get("/healthz", headers={"Host": host})

    assert response.status_code == 400
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "study.example",
        "unrelated.example",
        ".study.example",
        "chapter..study.example",
        "-chapter.study.example",
        "chapter-.study.example",
        r"chapter\.study.example",
        "chapter,notes.study.example",
        "chapter_notes.study.example",
    ],
)
async def test_wildcard_host_rejects_apex_unrelated_and_malformed_dns_names(
    tmp_path: Path,
    host: str,
) -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        allowed_hosts=("*.study.example",),
        local_storage_root=tmp_path / "storage",
    )
    app = create_app(settings=settings, storage=LocalStorage(tmp_path / "storage"))
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://notes.study.example") as client:
        response = await client.get("/healthz", headers={"Host": host})

    assert response.status_code == 400
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_wildcard_host_accepts_a_valid_dns_subdomain(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.TEST,
        allowed_hosts=("*.study.example",),
        local_storage_root=tmp_path / "storage",
    )
    app = create_app(settings=settings, storage=LocalStorage(tmp_path / "storage"))
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://notes.study.example") as client:
        response = await client.get(
            "/healthz",
            headers={"Host": "chapter-1.notes.study.example"},
        )

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_failed_settings_assignment_cannot_enable_wildcard_host(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.LOCAL,
        local_storage_root=tmp_path / "storage",
    )
    with pytest.raises(ValidationError, match="frozen"):
        settings.allowed_hosts = ("*",)

    app = create_app(settings=settings, storage=LocalStorage(tmp_path / "storage"))
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        response = await client.get("/healthz", headers={"Host": "attacker.example"})

    assert settings.allowed_hosts == ()
    assert response.status_code == 400
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_run_uses_validated_bind_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(application: str, **kwargs: object) -> None:
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("BIND_HOST", "::1")
    monkeypatch.setenv("BIND_PORT", "8123")
    monkeypatch.setattr("study_agent.main.uvicorn.run", fake_run)

    run()

    assert captured == {
        "application": "study_agent.main:app",
        "host": "::1",
        "port": 8123,
        "reload": False,
    }


def test_production_app_refuses_local_principal_fallback(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_mode=AppMode.PRODUCTION,
        auth_provider="oidc",
        auth_issuer="https://issuer.example",
        auth_audience="study-agent",
        allowed_hosts=("study.example",),
        allowed_origins=("https://study.example",),
        worker_token=SecretStr("correct-horse-battery-staple-8ca7"),
        local_storage_root=tmp_path / "storage",
    )

    with pytest.raises(RuntimeError, match="production principal provider"):
        create_app(settings=settings, storage=LocalStorage(tmp_path / "storage"))


@pytest.mark.asyncio
async def test_expensive_endpoint_rate_limit_is_bounded_per_client(tmp_path: Path) -> None:
    app = _local_app(tmp_path, query_requests_per_minute=1)
    transport = ASGITransport(app=app, client=("127.0.0.1", 51000))
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        first = await client.post("/api/v1/courses/course/queries", json={})
        second = await client.post("/api/v1/courses/course/queries", json={})

    assert first.status_code == 422
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0
