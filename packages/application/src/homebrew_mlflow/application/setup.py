from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from homebrew_mlflow.domain import (
    Organization,
    OrganizationMembership,
    OrganizationRole,
    PublicId,
    ResourceKind,
)

from .projects import AuthorizationDenied, ResourceConflict


class SetupStore(Protocol):
    def is_claimed(self) -> bool: ...

    def add_claim(
        self,
        organization: Organization,
        membership: OrganizationMembership,
        claimed_at: datetime,
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ClaimInstallation:
    principal_id: PublicId
    organization_name: str
    bootstrap_token: str
    claimed_at: datetime


class SetupService:
    def __init__(self, store: SetupStore, bootstrap_token_digest: str) -> None:
        self._store = store
        self._expected_digest = bootstrap_token_digest

    def claim(self, command: ClaimInstallation) -> Organization:
        if command.principal_id.kind is not ResourceKind.PRINCIPAL:
            raise ValueError("installation claimant must be a Principal")
        presented = hashlib.sha256(command.bootstrap_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(presented, self._expected_digest):
            raise AuthorizationDenied("invalid installation bootstrap token")
        if self._store.is_claimed():
            raise ResourceConflict("installation has already been claimed")
        organization = Organization.create(command.organization_name)
        membership = OrganizationMembership(
            organization.id,
            command.principal_id,
            OrganizationRole.ADMIN,
            command.claimed_at,
        )
        self._store.add_claim(organization, membership, command.claimed_at)
        self._store.commit()
        return organization
