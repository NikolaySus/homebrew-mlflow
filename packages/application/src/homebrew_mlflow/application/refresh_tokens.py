from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from homebrew_mlflow.domain import PublicId


class RefreshFailure(ValueError):
    pass


class RefreshReuseDetected(RefreshFailure):
    pass


class RotationStatus(StrEnum):
    ROTATED = "rotated"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    REUSED = "reused"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class NewRefreshCredential:
    digest: str
    family_id: UUID
    principal_id: PublicId
    sequence: int
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RotationResult:
    status: RotationStatus
    family_id: UUID | None = None
    principal_id: PublicId | None = None
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class RotatedRefreshCredential:
    token: str
    principal_id: PublicId


class RefreshCredentialStore(Protocol):
    def add(self, credential: NewRefreshCredential) -> None: ...

    def rotate(
        self,
        presented_digest: str,
        replacement_digest: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RotationResult: ...

    def revoke_family(self, family_id: UUID, now: datetime) -> None: ...

    def revoke_all(self, principal_id: PublicId, now: datetime) -> None: ...

    def family_for_digest(self, digest: str) -> UUID | None: ...

    def principal_for_digest(self, digest: str) -> PublicId | None: ...


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _secret() -> str:
    return "hmrf_" + secrets.token_urlsafe(32)


class RefreshCredentialService:
    def __init__(self, store: RefreshCredentialStore, lifetime: timedelta = timedelta(days=30)):
        self._store = store
        self._lifetime = lifetime

    def issue(self, principal_id: PublicId, now: datetime | None = None) -> str:
        issued_at = now or datetime.now(UTC)
        token = _secret()
        self._store.add(
            NewRefreshCredential(
                digest=_digest(token),
                family_id=uuid4(),
                principal_id=principal_id,
                sequence=0,
                issued_at=issued_at,
                expires_at=issued_at + self._lifetime,
            )
        )
        return token

    def rotate(self, token: str, now: datetime | None = None) -> str:
        return self.rotate_with_identity(token, now).token

    def rotate_with_identity(
        self, token: str, now: datetime | None = None
    ) -> RotatedRefreshCredential:
        if not token.startswith("hmrf_"):
            raise RefreshFailure("invalid refresh credential")
        rotated_at = now or datetime.now(UTC)
        replacement_token = _secret()
        result = self._store.rotate(
            _digest(token),
            _digest(replacement_token),
            rotated_at,
            rotated_at + self._lifetime,
        )
        if result.status is RotationStatus.REUSED:
            if result.family_id is not None:
                self._store.revoke_family(result.family_id, rotated_at)
            raise RefreshReuseDetected("refresh credential reuse detected")
        if result.status is not RotationStatus.ROTATED:
            raise RefreshFailure("refresh credential is invalid, expired, or revoked")
        if result.principal_id is None:
            raise RuntimeError("rotated refresh credential omitted its principal")
        return RotatedRefreshCredential(replacement_token, result.principal_id)

    def revoke(self, token: str, now: datetime | None = None) -> PublicId | None:
        if not token.startswith("hmrf_"):
            return None
        digest = _digest(token)
        family_id = self._store.family_for_digest(digest)
        principal_id = self._store.principal_for_digest(digest)
        if family_id is not None:
            self._store.revoke_family(family_id, now or datetime.now(UTC))
        return principal_id

    def revoke_all(self, principal_id: PublicId, now: datetime | None = None) -> None:
        self._store.revoke_all(principal_id, now or datetime.now(UTC))
