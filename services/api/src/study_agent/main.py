import re
from ipaddress import ip_address
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from study_agent import __version__
from study_agent.api.errors import (
    ApiProblem,
    api_problem_handler,
    request_validation_handler,
)
from study_agent.api.health import router as health_router
from study_agent.api.rate_limit import SlidingWindowLimiter
from study_agent.api.routers.courses import router as courses_router
from study_agent.api.routers.job_events import router as job_events_router
from study_agent.api.routers.notes import router as notes_router
from study_agent.api.routers.queries import router as queries_router
from study_agent.api.routers.sources import router as sources_router
from study_agent.api.routers.worker import router as worker_router
from study_agent.api.routers.workspace import router as workspace_router
from study_agent.config import AppMode, Settings, normalize_host
from study_agent.identity.principal import LocalPrincipalProvider, PrincipalProvider
from study_agent.infrastructure.db.session import Database
from study_agent.modules.answering.retrieval import (
    PostgresQueryEvidence,
    QueryEvidence,
)
from study_agent.modules.answering.source_tokens import LocalReadTokenSigner
from study_agent.modules.jobs.clock import SystemClock
from study_agent.modules.jobs.presence import WorkerPresenceRegistry
from study_agent.modules.jobs.waiter import AsyncioClaimWaiter, ClaimWaiter
from study_agent.modules.retrieval.bm25_index import Bm25IndexStore
from study_agent.modules.retrieval.dense import DenseRetriever
from study_agent.modules.retrieval.hybrid import HybridRetriever, PostgresEvidenceRepository
from study_agent.modules.retrieval.lexical import LexicalRetriever
from study_agent.modules.retrieval.rerank import RerankService
from study_agent.modules.retrieval.tokenizer import ChineseTokenizer
from study_agent.modules.retrieval.trace import PostgresTraceStore
from study_agent.observability.trace import trace_context
from study_agent.providers.factory import ProviderRegistry, build_provider_registry
from study_agent.providers.protocols import Clock
from study_agent.storage.local import LocalStorage


async def add_trace_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
    with trace_context() as trace_id:
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response


async def enforce_http_security(request: Request, call_next: RequestResponseEndpoint) -> Response:
    settings = request.app.state.settings
    request_host = _validated_request_host(request)
    if request_host is None or not _host_is_allowed(
        request_host,
        settings.effective_allowed_hosts,
    ):
        return _boundary_rejection(400, "request host is not allowed")
    if settings.app_mode is AppMode.LOCAL:
        forwarded_headers = (
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
        )
        if any(request.headers.get(header) is not None for header in forwarded_headers):
            return _boundary_rejection(400, "forwarded headers are not trusted in local mode")
    if settings.app_mode is not AppMode.TEST and request.method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        origin = request.headers.get("origin")
        if (
            origin is not None
            and origin.rstrip("/").lower() not in settings.effective_allowed_origins
        ):
            return _boundary_rejection(403, "request origin is not allowed")
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return _boundary_rejection(403, "cross-site requests are not allowed")

    bucket = _expensive_request_bucket(request)
    if bucket is not None:
        if request.client is None:
            return _boundary_rejection(401, "client identity is unavailable")
        bucket_name, limit = bucket
        allowed, retry_after = await request.app.state.abuse_limiter.allow(
            f"{bucket_name}:{request.client.host}",
            limit,
        )
        if not allowed:
            limited_response = _boundary_rejection(429, "request rate limit exceeded")
            limited_response.headers["Retry-After"] = str(retry_after)
            return limited_response

    response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    if settings.app_mode is AppMode.PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def _validated_request_host(request: Request) -> str | None:
    host_headers = [
        value for name, value in request.scope.get("headers", ()) if name.lower() == b"host"
    ]
    if len(host_headers) != 1:
        return None
    try:
        raw_host = host_headers[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    if (
        not raw_host
        or raw_host != raw_host.strip()
        or "%" in raw_host
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in raw_host)
    ):
        return None

    try:
        parsed = urlsplit(f"//{raw_host}")
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        return None

    if raw_host.startswith("["):
        closing_bracket = raw_host.find("]")
        if closing_bracket < 0 or raw_host.count("[") != 1 or raw_host.count("]") != 1:
            return None
        literal = raw_host[1:closing_bracket]
        remainder = raw_host[closing_bracket + 1 :]
        if remainder and (not remainder.startswith(":") or not remainder[1:].isdigit()):
            return None
        try:
            address = ip_address(literal)
        except ValueError:
            return None
        if address.version != 6 or parsed.hostname.lower() != literal.lower():
            return None
        return address.compressed

    if "[" in raw_host or "]" in raw_host or raw_host.count(":") > 1:
        return None
    if ":" in raw_host:
        hostname, port_text = raw_host.rsplit(":", 1)
        if not hostname or not port_text.isdigit():
            return None
    else:
        hostname = raw_host
    if parsed.hostname.lower() != hostname.lower():
        return None
    try:
        return normalize_host(parsed.hostname)
    except ValueError:
        return None


def _host_is_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    for allowed_host in allowed_hosts:
        if allowed_host == "*" or host == allowed_host:
            return True
        if allowed_host.startswith("*.") and host.endswith(allowed_host[1:]):
            return host != allowed_host[2:]
    return False


def _boundary_rejection(status_code: int, title: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": "REQUEST_BOUNDARY_REJECTED", "title": title},
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


_QUERY_PATH = re.compile(r"^/api/v1/courses/[^/]+/queries$")
_UPLOAD_DECLARATION_PATH = re.compile(r"^/api/v1/courses/[^/]+/documents$")
_UPLOAD_BYTES_PATH = re.compile(r"^/api/v1/uploads/[^/]+$")


def _expensive_request_bucket(request: Request) -> tuple[str, int] | None:
    settings = request.app.state.settings
    path = request.url.path
    if request.method == "POST" and _QUERY_PATH.fullmatch(path):
        return "provider-query", settings.query_requests_per_minute
    if (request.method == "POST" and _UPLOAD_DECLARATION_PATH.fullmatch(path)) or (
        request.method == "PUT" and _UPLOAD_BYTES_PATH.fullmatch(path)
    ):
        return "upload", settings.upload_requests_per_minute
    return None


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    storage: LocalStorage | None = None,
    principal_provider: PrincipalProvider | None = None,
    clock: Clock | None = None,
    claim_waiter: ClaimWaiter | None = None,
    provider_registry: ProviderRegistry | None = None,
    query_evidence: QueryEvidence | None = None,
    worker_presence: WorkerPresenceRegistry | None = None,
    local_read_signer: LocalReadTokenSigner | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    if resolved_settings.app_mode is AppMode.PRODUCTION:
        raise RuntimeError(
            "production principal provider is not implemented; local identity fallback is forbidden"
        )
    resolved_database = database or Database(resolved_settings.database_url.get_secret_value())
    resolved_storage = storage or LocalStorage(resolved_settings.local_storage_root)
    resolved_registry = provider_registry or build_provider_registry(resolved_settings)
    resolved_query_evidence = query_evidence
    if resolved_query_evidence is None:
        lexical = LexicalRetriever(
            resolved_database,
            Bm25IndexStore(
                resolved_settings.lexical_index_root,
                ChineseTokenizer(resolved_settings.course_terms),
            ),
        )
        hybrid = HybridRetriever(
            dense=DenseRetriever(resolved_database),
            lexical=lexical,
            evidence=PostgresEvidenceRepository(resolved_database),
            traces=PostgresTraceStore(resolved_database),
            reranker=RerankService(enabled=resolved_settings.reranker_enabled),
        )
        resolved_query_evidence = PostgresQueryEvidence(
            resolved_database,
            resolved_registry,
            hybrid,
        )
    application = FastAPI(
        title="期末复习智能体 API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )
    application.state.settings = resolved_settings
    application.state.database = resolved_database
    application.state.storage = resolved_storage
    application.state.principal_provider = principal_provider or LocalPrincipalProvider()
    application.state.clock = clock or SystemClock()
    application.state.claim_waiter = claim_waiter or AsyncioClaimWaiter()
    application.state.provider_registry = resolved_registry
    application.state.query_evidence = resolved_query_evidence
    application.state.worker_presence = worker_presence or WorkerPresenceRegistry()
    application.state.local_read_signer = local_read_signer or LocalReadTokenSigner()
    application.state.abuse_limiter = SlidingWindowLimiter()
    application.middleware("http")(add_trace_id)
    application.middleware("http")(enforce_http_security)
    application.add_exception_handler(ApiProblem, api_problem_handler)  # type: ignore[arg-type]
    application.add_exception_handler(
        RequestValidationError,
        request_validation_handler,  # type: ignore[arg-type]
    )
    application.include_router(health_router)
    application.include_router(courses_router)
    application.include_router(job_events_router)
    application.include_router(notes_router)
    application.include_router(queries_router)
    application.include_router(sources_router)
    application.include_router(worker_router)
    application.include_router(workspace_router)
    return application


app = create_app()


def run() -> None:
    settings = Settings()
    uvicorn.run(
        "study_agent.main:app",
        host=settings.bind_host,
        port=settings.bind_port,
        reload=False,
    )
