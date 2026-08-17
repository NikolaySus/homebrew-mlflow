import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    ClaimInstallation,
    ResourceConflict,
    SetupService,
)
from homebrew_mlflow.domain import Organization, OrganizationMembership, PublicId, ResourceKind

TOKEN = "one-time-bootstrap-token"
DIGEST = hashlib.sha256(TOKEN.encode()).hexdigest()


@dataclass
class MemorySetupStore:
    claimed: bool = False
    organization: Organization | None = None
    membership: OrganizationMembership | None = None
    committed: bool = False

    def is_claimed(self) -> bool:
        return self.claimed

    def add_claim(
        self,
        organization: Organization,
        membership: OrganizationMembership,
        _claimed_at: datetime,
    ) -> None:
        self.claimed = True
        self.organization = organization
        self.membership = membership

    def commit(self) -> None:
        self.committed = True


def command(token: str = TOKEN) -> ClaimInstallation:
    return ClaimInstallation(
        PublicId.generate(ResourceKind.PRINCIPAL),
        "Research Organization",
        token,
        datetime(2026, 8, 17, tzinfo=UTC),
    )


def test_valid_token_claims_installation_once_and_assigns_admin() -> None:
    store = MemorySetupStore()
    organization = SetupService(store, DIGEST).claim(command())

    assert store.organization == organization
    assert store.membership is not None
    assert store.membership.role.value == "admin"
    assert store.committed


def test_invalid_token_and_second_claim_are_rejected() -> None:
    with pytest.raises(AuthorizationDenied):
        SetupService(MemorySetupStore(), DIGEST).claim(command("wrong"))

    store = MemorySetupStore(claimed=True)
    with pytest.raises(ResourceConflict):
        SetupService(store, DIGEST).claim(command())
