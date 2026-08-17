from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from homebrew_mlflow.domain import MachineScope, PublicId, ResourceKind


class TokenAudience(StrEnum):
    PLATFORM_API = "platform-api"
    MLFLOW = "mlflow"
    PUBLICATION = "publication"
    DVC_CREDENTIALS = "dvc-credentials"


class AccessTokenFailure(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    principal_id: PublicId
    audience: TokenAudience
    scopes: frozenset[MachineScope]
    issued_at: datetime
    expires_at: datetime
    project_id: PublicId | None = None
    run_id: PublicId | None = None


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AccessTokenService:
    def __init__(
        self,
        signing_key: str,
        issuer: str,
        *,
        key_id: str = "v1",
        lifetime: timedelta = timedelta(minutes=20),
    ) -> None:
        if len(signing_key.encode("utf-8")) < 32:
            raise ValueError("access-token signing key must contain at least 32 bytes")
        self._key = signing_key.encode("utf-8")
        self._issuer = issuer
        self._key_id = key_id
        self._lifetime = lifetime

    def issue(
        self,
        principal_id: PublicId,
        audience: TokenAudience,
        *,
        project_id: PublicId | None = None,
        run_id: PublicId | None = None,
        scopes: frozenset[MachineScope] = frozenset(),
        now: datetime | None = None,
        lifetime: timedelta | None = None,
    ) -> str:
        if principal_id.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("access token subject must be a Principal")
        if project_id is not None and project_id.kind is not ResourceKind.PROJECT:
            raise ValueError("access token project binding must be a Research Project")
        if run_id is not None and run_id.kind is not ResourceKind.RUN:
            raise ValueError("access token run binding must be a Run")
        if run_id is not None and project_id is None:
            raise ValueError("run-bound access tokens must also be project-bound")
        issued_at = now or datetime.now(UTC)
        token_lifetime = lifetime or self._lifetime
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "sub": str(principal_id),
            "aud": audience.value,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + token_lifetime).timestamp()),
            "scp": sorted(scope.value for scope in scopes),
        }
        if project_id is not None:
            payload["prj"] = str(project_id)
        if run_id is not None:
            payload["run"] = str(run_id)
        header = {"alg": "HS256", "typ": "JWT", "kid": self._key_id}
        signing_input = ".".join(
            (
                _encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
                _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
            )
        )
        signature = hmac.new(self._key, signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_encode(signature)}"

    def verify(
        self,
        token: str,
        audience: TokenAudience,
        *,
        now: datetime | None = None,
    ) -> AccessTokenClaims:
        try:
            header_part, payload_part, signature_part = token.split(".")
            signing_input = f"{header_part}.{payload_part}"
            expected = hmac.new(self._key, signing_input.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode(signature_part)):
                raise AccessTokenFailure("invalid access token signature")
            header = json.loads(_decode(header_part))
            payload = json.loads(_decode(payload_part))
            if header != {"alg": "HS256", "kid": self._key_id, "typ": "JWT"}:
                raise AccessTokenFailure("unsupported access token header")
            if payload["iss"] != self._issuer or payload["aud"] != audience.value:
                raise AccessTokenFailure("access token issuer or audience mismatch")
            current = now or datetime.now(UTC)
            issued_at = datetime.fromtimestamp(int(payload["iat"]), UTC)
            expires_at = datetime.fromtimestamp(int(payload["exp"]), UTC)
            if expires_at <= current or issued_at > current + timedelta(seconds=30):
                raise AccessTokenFailure("access token is expired or not yet valid")
            principal_id = PublicId(ResourceKind.PRINCIPAL, payload["sub"])
            project_value = payload.get("prj")
            project_id = (
                PublicId(ResourceKind.PROJECT, project_value) if project_value is not None else None
            )
            run_value = payload.get("run")
            run_id = PublicId(ResourceKind.RUN, run_value) if run_value is not None else None
            if run_id is not None and project_id is None:
                raise AccessTokenFailure("run-bound access token is missing its project binding")
            scopes = frozenset(MachineScope(scope) for scope in payload.get("scp", []))
        except AccessTokenFailure:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            raise AccessTokenFailure("malformed access token") from error
        return AccessTokenClaims(
            principal_id=principal_id,
            audience=audience,
            scopes=scopes,
            issued_at=issued_at,
            expires_at=expires_at,
            project_id=project_id,
            run_id=run_id,
        )
