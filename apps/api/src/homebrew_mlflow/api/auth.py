from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

import httpx
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from homebrew_mlflow.application import (
    AccessTokenClaims,
    RefreshCredentialService,
    TokenAudience,
)
from homebrew_mlflow.domain import AuditEvent, MachineScope, PublicId, ResourceKind, permits
from homebrew_mlflow.infrastructure import (
    DevicePollStatus,
    GitLabDeviceOAuthClient,
    SqlAlchemyGitLabIdentityStore,
    SqlAlchemyProjectUnitOfWork,
    SqlAlchemyRefreshCredentialStore,
    SqlAlchemyRepositoryUnitOfWork,
    create_session,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from .security import access_tokens, platform_claims
from .settings import get_settings

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


def _audit_authentication(
    session: Session,
    principal_id: PublicId,
    action: str,
    request_id: str,
    now: datetime,
    *,
    project_id: PublicId | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    writer = SqlAlchemyProjectUnitOfWork(session)
    writer.append_audit(
        AuditEvent(
            actor_principal_id=principal_id,
            action=action,
            resource_type="platform_session",
            resource_id=principal_id,
            outcome="success",
            request_id=PublicId(ResourceKind.REQUEST, request_id),
            project_id=project_id,
            safe_metadata=metadata or {},
            occurred_at=now,
        )
    )
    writer.commit()


class DeviceStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DevicePollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_code: str


class PendingDeviceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["authorization_pending", "slow_down"]


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = 1200


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class ExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: TokenAudience
    project_id: str
    scopes: list[MachineScope]


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = 1200


def _web_callback_url() -> str:
    return f"{str(get_settings().public_base_url).rstrip('/')}/api/v1/auth/web/callback"


def _sign_web_payload(payload: dict[str, object]) -> str:
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        get_settings().access_token_signing_key.get_secret_value().encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def _verify_web_payload(value: str) -> dict[str, object]:
    try:
        encoded, signature = value.rsplit(".", 1)
        expected = hmac.new(
            get_settings().access_token_signing_key.get_secret_value().encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(decoded, dict) or int(decoded["exp"]) < int(time.time()):
            raise ValueError
        return decoded
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="invalid_oauth_state") from error


def _oauth_state(nonce: str) -> str:
    signature = hmac.new(
        get_settings().access_token_signing_key.get_secret_value().encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def _oauth_cookie(return_to: str, nonce: str, verifier: str) -> str:
    if not return_to.startswith("/") or return_to.startswith("//"):
        raise HTTPException(status_code=400, detail="invalid_return_path")
    return _sign_web_payload(
        {
            "nonce": nonce,
            "return_to": return_to,
            "verifier": verifier,
            "exp": int(time.time()) + 600,
        }
    )


def _verify_oauth_context(cookie: str | None, state: str) -> tuple[str, str]:
    if cookie is None:
        raise HTTPException(status_code=400, detail="invalid_oauth_state")
    payload = _verify_web_payload(cookie)
    nonce = str(payload.get("nonce", ""))
    if not hmac.compare_digest(_oauth_state(nonce), state):
        raise HTTPException(status_code=400, detail="invalid_oauth_state")
    return_to = str(payload.get("return_to", ""))
    verifier = str(payload.get("verifier", ""))
    if (
        not return_to.startswith("/")
        or return_to.startswith("//")
        or not 43 <= len(verifier) <= 128
    ):
        raise HTTPException(status_code=400, detail="invalid_oauth_state")
    return return_to, verifier


def _csrf_guard(header_token: str | None, cookie_token: str | None) -> None:
    if (
        header_token is None
        or cookie_token is None
        or not hmac.compare_digest(header_token, cookie_token)
    ):
        raise HTTPException(status_code=403, detail="csrf_validation_failed")


@router.get("/web/start")
def web_start(return_to: Annotated[str, Query()] = "/") -> RedirectResponse:
    settings = get_settings()
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    nonce = secrets.token_urlsafe(24)
    parameters = urlencode(
        {
            "client_id": settings.gitlab_oauth_client_id,
            "redirect_uri": _web_callback_url(),
            "response_type": "code",
            "scope": "read_user",
            "state": _oauth_state(nonce),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    response = RedirectResponse(
        f"{str(settings.gitlab_public_base_url).rstrip('/')}/oauth/authorize?{parameters}"
    )
    response.set_cookie(
        "hm_oauth",
        _oauth_cookie(return_to, nonce, verifier),
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=600,
        path="/api/v1/auth/web/callback",
    )
    return response


@router.get("/web/callback")
def web_callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    request: Request,
    hm_oauth: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    return_to, verifier = _verify_oauth_context(hm_oauth, state)
    settings = get_settings()
    token = httpx.post(
        f"{str(settings.gitlab_base_url).rstrip('/')}/oauth/token",
        data={
            "client_id": settings.gitlab_oauth_client_id,
            "client_secret": settings.gitlab_oauth_client_secret.get_secret_value(),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _web_callback_url(),
            "code_verifier": verifier,
        },
        timeout=20,
    )
    token.raise_for_status()
    gitlab_access = token.json().get("access_token")
    if not isinstance(gitlab_access, str):
        raise HTTPException(status_code=502, detail="gitlab_identity_missing")
    try:
        identity = httpx.get(
            f"{str(settings.gitlab_base_url).rstrip('/')}/api/v4/user",
            headers={"Authorization": f"Bearer {gitlab_access}"},
            timeout=20,
        )
        identity.raise_for_status()
    finally:
        httpx.post(
            f"{str(settings.gitlab_base_url).rstrip('/')}/oauth/revoke",
            data={
                "client_id": settings.gitlab_oauth_client_id,
                "client_secret": settings.gitlab_oauth_client_secret.get_secret_value(),
                "token": gitlab_access,
            },
            timeout=20,
        ).raise_for_status()
    payload = identity.json()
    now = datetime.now(UTC)
    with create_session(settings.database_url) as session:
        principal = SqlAlchemyGitLabIdentityStore(session).resolve_or_create(
            str(payload["id"]),
            str(payload["username"]),
            str(payload["email"]),
            str(payload.get("name") or payload["username"]),
            now,
        )
        refresh_token = RefreshCredentialService(SqlAlchemyRefreshCredentialStore(session)).issue(
            principal.id, now
        )
        _audit_authentication(
            session, principal.id, "authentication.web", request.state.request_id, now
        )
    response = RedirectResponse(return_to, status_code=303)
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        "hm_refresh",
        refresh_token,
        httponly=True,
        secure=settings.environment == "production",
        samesite="lax",
        max_age=30 * 24 * 3600,
        path="/api/v1/auth",
    )
    response.set_cookie(
        "hm_csrf",
        csrf_token,
        httponly=False,
        secure=settings.environment == "production",
        samesite="strict",
        max_age=30 * 24 * 3600,
        path="/",
    )
    response.delete_cookie("hm_oauth", path="/api/v1/auth/web/callback")
    return response


@router.post("/web/session", response_model=AccessTokenResponse)
def web_session(
    response: Response,
    request: Request,
    hm_refresh: Annotated[str | None, Cookie()] = None,
    hm_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> AccessTokenResponse:
    _csrf_guard(x_csrf_token, hm_csrf)
    if hm_refresh is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    now = datetime.now(UTC)
    with create_session(get_settings().database_url) as session:
        rotated = RefreshCredentialService(
            SqlAlchemyRefreshCredentialStore(session)
        ).rotate_with_identity(hm_refresh, now)
        _audit_authentication(
            session,
            rotated.principal_id,
            "authentication.refresh",
            request.state.request_id,
            now,
            metadata={"client": "web"},
        )
    response.set_cookie(
        "hm_refresh",
        rotated.token,
        httponly=True,
        secure=get_settings().environment == "production",
        samesite="lax",
        max_age=30 * 24 * 3600,
        path="/api/v1/auth",
    )
    return AccessTokenResponse(
        access_token=access_tokens().issue(
            rotated.principal_id, TokenAudience.PLATFORM_API, now=now
        )
    )


@router.post("/web/logout", status_code=status.HTTP_204_NO_CONTENT)
def web_logout(
    response: Response,
    request: Request,
    hm_refresh: Annotated[str | None, Cookie()] = None,
    hm_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> Response:
    _csrf_guard(x_csrf_token, hm_csrf)
    if hm_refresh is not None:
        now = datetime.now(UTC)
        with create_session(get_settings().database_url) as session:
            principal_id = RefreshCredentialService(
                SqlAlchemyRefreshCredentialStore(session)
            ).revoke(
                hm_refresh, now
            )
            if principal_id is not None:
                _audit_authentication(
                    session,
                    principal_id,
                    "authentication.logout",
                    request.state.request_id,
                    now,
                    metadata={"client": "web"},
                )
    response.delete_cookie("hm_refresh", path="/api/v1/auth")
    response.delete_cookie("hm_csrf", path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _gitlab_client() -> GitLabDeviceOAuthClient:
    settings = get_settings()
    return GitLabDeviceOAuthClient(
        str(settings.gitlab_base_url),
        settings.gitlab_device_oauth_client_id,
        settings.gitlab_device_oauth_client_secret.get_secret_value(),
        public_base_url=str(settings.gitlab_public_base_url),
    )


@router.post("/device/start", response_model=DeviceStartResponse)
def device_start() -> DeviceStartResponse:
    started = _gitlab_client().start()
    return DeviceStartResponse(
        device_code=started.device_code,
        user_code=started.user_code,
        verification_uri=started.verification_uri,
        verification_uri_complete=started.verification_uri_complete,
        expires_in=started.expires_in,
        interval=started.interval,
    )


@router.post(
    "/device/poll",
    response_model=SessionResponse | PendingDeviceResponse,
    status_code=status.HTTP_200_OK,
)
def device_poll(
    body: DevicePollRequest, request: Request
) -> SessionResponse | PendingDeviceResponse:
    result = _gitlab_client().poll(body.device_code)
    if result.status is DevicePollStatus.AUTHORIZATION_PENDING:
        return PendingDeviceResponse(status="authorization_pending")
    if result.status is DevicePollStatus.SLOW_DOWN:
        return PendingDeviceResponse(status="slow_down")
    if result.status is not None:
        raise HTTPException(status_code=400, detail=result.status.value)
    if result.identity is None:
        raise HTTPException(status_code=502, detail="gitlab_identity_missing")

    settings = get_settings()
    now = datetime.now(UTC)
    with create_session(settings.database_url) as session:
        principal = SqlAlchemyGitLabIdentityStore(session).resolve_or_create(
            result.identity.subject,
            result.identity.username,
            result.identity.email,
            result.identity.display_name,
            now,
        )
        refresh = RefreshCredentialService(SqlAlchemyRefreshCredentialStore(session)).issue(
            principal.id, now
        )
        _audit_authentication(
            session, principal.id, "authentication.device", request.state.request_id, now
        )
    access = access_tokens().issue(principal.id, TokenAudience.PLATFORM_API, now=now)
    return SessionResponse(
        principal_id=str(principal.id), access_token=access, refresh_token=refresh
    )


@router.post("/refresh", response_model=SessionResponse)
def refresh(body: RefreshRequest, request: Request) -> SessionResponse:
    settings = get_settings()
    now = datetime.now(UTC)
    with create_session(settings.database_url) as session:
        rotated = RefreshCredentialService(
            SqlAlchemyRefreshCredentialStore(session)
        ).rotate_with_identity(body.refresh_token, now)
        _audit_authentication(
            session,
            rotated.principal_id,
            "authentication.refresh",
            request.state.request_id,
            now,
            metadata={"client": "api"},
        )
    access = access_tokens().issue(rotated.principal_id, TokenAudience.PLATFORM_API, now=now)
    return SessionResponse(
        principal_id=str(rotated.principal_id),
        access_token=access,
        refresh_token=rotated.token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest, request: Request) -> Response:
    now = datetime.now(UTC)
    with create_session(get_settings().database_url) as session:
        principal_id = RefreshCredentialService(SqlAlchemyRefreshCredentialStore(session)).revoke(
            body.refresh_token, now
        )
        if principal_id is not None:
            _audit_authentication(
                session,
                principal_id,
                "authentication.logout",
                request.state.request_id,
                now,
                metadata={"client": "api"},
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/revoke-all", status_code=status.HTTP_204_NO_CONTENT)
def revoke_all(
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> Response:
    now = datetime.now(UTC)
    request_id = PublicId(ResourceKind.REQUEST, request.state.request_id)
    with create_session(get_settings().database_url) as session:
        RefreshCredentialService(SqlAlchemyRefreshCredentialStore(session)).revoke_all(
            claims.principal_id, now
        )
        audit = SqlAlchemyProjectUnitOfWork(session)
        audit.append_audit(
            AuditEvent(
                actor_principal_id=claims.principal_id,
                action="authentication.revoke_all",
                resource_type="principal_sessions",
                resource_id=claims.principal_id,
                outcome="success",
                request_id=request_id,
                safe_metadata={},
                occurred_at=now,
            )
        )
        audit.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


_AUDIENCE_SCOPES = {
    TokenAudience.MLFLOW: frozenset({MachineScope.TRACK}),
    TokenAudience.PUBLICATION: frozenset({MachineScope.PUBLISH}),
    TokenAudience.DVC_CREDENTIALS: frozenset({MachineScope.DVC_TRANSFER}),
}


@router.post("/exchange", response_model=AccessTokenResponse)
def exchange(
    body: ExchangeRequest,
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(platform_claims)],
) -> AccessTokenResponse:
    allowed = _AUDIENCE_SCOPES.get(body.audience)
    requested = frozenset(body.scopes)
    if allowed is None or not requested or not requested <= allowed:
        raise HTTPException(status_code=422, detail="invalid_audience_scope")
    try:
        project_id = PublicId(ResourceKind.PROJECT, body.project_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="project_not_found") from error
    settings = get_settings()
    with create_session(settings.database_url) as session:
        role = SqlAlchemyRepositoryUnitOfWork(session).project_role(project_id, claims.principal_id)
    if role is None or any(not permits(role, scope) for scope in requested):
        raise HTTPException(status_code=403, detail="forbidden")
    if claims.scopes and not requested <= claims.scopes:
        raise HTTPException(status_code=403, detail="forbidden")
    token = access_tokens().issue(
        claims.principal_id,
        body.audience,
        project_id=project_id,
        scopes=requested,
    )
    with create_session(settings.database_url) as session:
        _audit_authentication(
            session,
            claims.principal_id,
            "credential.exchange",
            request.state.request_id,
            datetime.now(UTC),
            project_id=project_id,
            metadata={
                "audience": body.audience.value,
                "scopes": sorted(scope.value for scope in requested),
            },
        )
    return AccessTokenResponse(access_token=token)
