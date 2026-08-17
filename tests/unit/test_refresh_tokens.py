from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from homebrew_mlflow.application import (
    NewRefreshCredential,
    RefreshCredentialService,
    RefreshFailure,
    RefreshReuseDetected,
    RotationResult,
    RotationStatus,
)
from homebrew_mlflow.domain import Principal, PrincipalKind, PublicId


class MemoryRefreshStore:
    def __init__(self) -> None:
        self.records: dict[str, NewRefreshCredential] = {}
        self.used: set[str] = set()
        self.revoked: set[UUID] = set()

    def add(self, credential: NewRefreshCredential) -> None:
        self.records[credential.digest] = credential

    def rotate(
        self,
        presented_digest: str,
        replacement_digest: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> RotationResult:
        record = self.records.get(presented_digest)
        if record is None:
            return RotationResult(RotationStatus.NOT_FOUND)
        if presented_digest in self.used:
            return RotationResult(RotationStatus.REUSED, family_id=record.family_id)
        if record.family_id in self.revoked:
            return RotationResult(RotationStatus.REVOKED, family_id=record.family_id)
        if record.expires_at <= issued_at:
            return RotationResult(RotationStatus.EXPIRED, family_id=record.family_id)
        self.used.add(presented_digest)
        self.records[replacement_digest] = replace(
            record,
            digest=replacement_digest,
            sequence=record.sequence + 1,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return RotationResult(
            RotationStatus.ROTATED,
            family_id=record.family_id,
            principal_id=record.principal_id,
            sequence=record.sequence + 1,
        )

    def revoke_family(self, family_id: UUID, now: datetime) -> None:
        self.revoked.add(family_id)

    def revoke_all(self, principal_id: PublicId, now: datetime) -> None:
        self.revoked.update(
            record.family_id
            for record in self.records.values()
            if record.principal_id == principal_id
        )

    def family_for_digest(self, digest: str) -> UUID | None:
        record = self.records.get(digest)
        return record.family_id if record is not None else None

    def principal_for_digest(self, digest: str) -> PublicId | None:
        record = self.records.get(digest)
        return record.principal_id if record is not None else None


def test_refresh_rotates_and_reuse_revokes_entire_family() -> None:
    store = MemoryRefreshStore()
    service = RefreshCredentialService(store)
    principal = Principal.create(PrincipalKind.HUMAN, "Researcher")
    now = datetime(2026, 8, 13, tzinfo=UTC)
    first = service.issue(principal.id, now)
    second = service.rotate(first, now)
    assert second != first

    with pytest.raises(RefreshReuseDetected):
        service.rotate(first, now)
    with pytest.raises(RefreshFailure):
        service.rotate(second, now)


def test_malformed_refresh_is_rejected_without_store_lookup() -> None:
    with pytest.raises(RefreshFailure, match="invalid"):
        RefreshCredentialService(MemoryRefreshStore()).rotate("not-a-token")


def test_revoke_all_invalidates_every_refresh_family_for_principal() -> None:
    store = MemoryRefreshStore()
    service = RefreshCredentialService(store)
    principal = Principal.create(PrincipalKind.HUMAN, "Researcher")
    now = datetime(2026, 8, 13, tzinfo=UTC)
    first = service.issue(principal.id, now)
    second = service.issue(principal.id, now)

    service.revoke_all(principal.id, now)

    with pytest.raises(RefreshFailure):
        service.rotate(first, now)
    with pytest.raises(RefreshFailure):
        service.rotate(second, now)
