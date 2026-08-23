from __future__ import annotations

import json
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, PlainTextResponse
from homebrew_mlflow.application import (
    AuthorizationDenied,
    RefreshFailure,
    RefreshReuseDetected,
    ResourceConflict,
)
from homebrew_mlflow.contracts import ClientRelease, ClientReleaseResponse, load_openapi
from homebrew_mlflow.domain import PublicId, ResourceKind
from homebrew_mlflow.infrastructure import database_is_ready
from pydantic import AnyHttpUrl
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse

from .artifacts import router as artifacts_router
from .attachments import router as attachments_router
from .audit import router as audit_router
from .auth import router as auth_router
from .diagnostics import router as diagnostics_router
from .dvc_credentials import router as dvc_credentials_router
from .environments import router as environments_router
from .identity import router as identity_router
from .machine_credentials import router as machine_credentials_router
from .memberships import router as memberships_router
from .mlflow_compat import router as mlflow_compat_router
from .observability import (
    RequestRateLimiter,
    log_request,
    prometheus_metrics,
    record_request,
)
from .organization_memberships import router as organization_memberships_router
from .pipelines import router as pipelines_router
from .projects import router as projects_router
from .publications import router as publications_router
from .runs import router as runs_router
from .secret_contexts import router as secret_contexts_router
from .settings import get_settings
from .setup import router as setup_router
from .sharing import router as sharing_router
from .tracking import router as tracking_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Homebrew MLflow API",
        version="0.1.0",
        openapi_version="3.2.0",
        docs_url=None,
        redoc_url=None,
    )
    app.openapi = load_openapi  # type: ignore[method-assign]
    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(dvc_credentials_router)
    app.include_router(diagnostics_router)
    app.include_router(environments_router)
    app.include_router(identity_router)
    app.include_router(attachments_router)
    app.include_router(machine_credentials_router)
    app.include_router(memberships_router)
    app.include_router(mlflow_compat_router)
    app.include_router(organization_memberships_router)
    app.include_router(pipelines_router)
    app.include_router(artifacts_router)
    app.include_router(projects_router)
    app.include_router(publications_router)
    app.include_router(runs_router)
    app.include_router(secret_contexts_router)
    app.include_router(setup_router)
    app.include_router(sharing_router)
    app.include_router(tracking_router)
    limiter = RequestRateLimiter()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        supplied = request.headers.get("X-Request-ID")
        try:
            request_id = str(PublicId(ResourceKind.REQUEST, supplied)) if supplied else None
        except ValueError:
            request_id = None
        request_id = request_id or str(PublicId.generate(ResourceKind.REQUEST))
        request.state.request_id = request_id
        if not limiter.permit(request):
            response = JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "The request rate limit was exceeded.",
                        "request_id": request_id,
                        "details": {},
                        "retryable": True,
                    }
                },
                headers={"Retry-After": "60"},
            )
            record_request(request.method, request.url.path, 429, time.monotonic() - started)
            return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        elapsed = time.monotonic() - started
        record_request(request.method, request.url.path, response.status_code, elapsed)
        log_request(request_id, request.method, request.url.path, response.status_code, elapsed)
        return response

    @app.get("/health/live", include_in_schema=True)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=True, response_model=None)
    async def ready(request: Request) -> dict[str, str] | JSONResponse:
        settings = get_settings()
        if not await run_in_threadpool(database_is_ready, settings.database_url):
            request_id = request.state.request_id
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "database_unavailable",
                        "message": "The service is not ready.",
                        "request_id": request_id,
                        "details": {},
                        "retryable": True,
                    }
                },
            )
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=True, response_class=PlainTextResponse)
    async def metrics() -> str:
        return prometheus_metrics()

    @app.get("/docs", include_in_schema=True, response_class=HTMLResponse)
    async def read_only_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="Homebrew MLflow API reference",
            swagger_ui_parameters={"supportedSubmitMethods": []},
        )

    @app.get(
        "/api/v1/client-releases/recommended",
        response_model=ClientReleaseResponse,
    )
    async def recommended_release() -> ClientReleaseResponse:
        settings = get_settings()
        version = settings.client_recommended_version
        base = str(settings.public_base_url).rstrip("/")
        index_url = f"{base}/packages/simple/"
        package = f"homebrew-mlflow=={version}"
        hashes: dict[str, str] = {}
        if settings.client_release_manifest is not None:
            manifest = json.loads(settings.client_release_manifest.read_text(encoding="utf-8"))
            hashes = {
                filename: digest
                for filename, digest in manifest["sha256"].items()
                if filename.startswith("homebrew_mlflow-")
            }
        return ClientReleaseResponse(
            release=ClientRelease(
                recommended_version=version,
                compatible_versions=settings.client_compatible_versions,
                requires_python=settings.client_requires_python,
                platforms=settings.client_platforms,
                index_url=AnyHttpUrl(index_url),
                sha256=hashes,
            ),
            install_commands={
                "uv": (
                    f'uv tool install --force --default-index {index_url} '
                    f'--no-build "{package}"'
                ),
                "pipx": f"pipx install --index-url {index_url} {package}",
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, _error: Exception) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "request_id": request_id,
                    "details": {},
                    "retryable": False,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(RefreshFailure)
    async def refresh_error(request: Request, error: RefreshFailure) -> JSONResponse:
        request_id = request.state.request_id
        code = (
            "refresh_reuse_detected" if isinstance(error, RefreshReuseDetected) else "unauthorized"
        )
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": code,
                    "message": "The platform session is invalid.",
                    "request_id": request_id,
                    "details": {},
                    "retryable": False,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        request_id = request.state.request_id
        detail = str(error.detail)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": detail if detail.replace("_", "").isalnum() else "request_failed",
                    "message": "The request could not be completed.",
                    "request_id": request_id,
                    "details": {},
                    "retryable": error.status_code >= 500,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(AuthorizationDenied)
    async def authorization_error(request: Request, _error: AuthorizationDenied) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "forbidden",
                    "message": "Access is denied.",
                    "request_id": request_id,
                    "details": {},
                    "retryable": False,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    @app.exception_handler(ResourceConflict)
    async def conflict_error(request: Request, _error: ResourceConflict) -> JSONResponse:
        request_id = request.state.request_id
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "resource_conflict",
                    "message": "The requested state conflicts with an existing resource.",
                    "request_id": request_id,
                    "details": {},
                    "retryable": False,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("homebrew_mlflow.api.main:app", host="0.0.0.0", port=8000)
