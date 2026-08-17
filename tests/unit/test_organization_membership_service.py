from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from homebrew_mlflow.application import (
    AuthorizationDenied,
    OrganizationMembershipService,
    OrganizationPrincipalView,
    ResourceConflict,
)
from homebrew_mlflow.domain import (
    AuditEvent,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    Principal,
    PrincipalKind,
    PublicId,
    ResourceKind,
)

NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


@dataclass
class OrganizationMembershipStore:
    organization: Organization
    people: dict[PublicId, Principal]
    roles: dict[PublicId, OrganizationMembership]
    audits: list[AuditEvent] = field(default_factory=list)

    def organization_role(
        self, _organization_id: PublicId, principal_id: PublicId
    ) -> OrganizationRole | None:
        membership = self.roles.get(principal_id)
        return membership.role if membership is not None else None

    def principal(self, principal_id: PublicId) -> Principal | None:
        return self.people.get(principal_id)

    def principals(
        self, _organization_id: PublicId
    ) -> tuple[OrganizationPrincipalView, ...]:
        return tuple(
            OrganizationPrincipalView(
                principal,
                None,
                self.roles[principal.id].role if principal.id in self.roles else None,
                self.roles[principal.id].created_at if principal.id in self.roles else None,
            )
            for principal in self.people.values()
        )

    def admin_count(self, _organization_id: PublicId) -> int:
        return sum(value.role is OrganizationRole.ADMIN for value in self.roles.values())

    def put_organization_membership(self, membership: OrganizationMembership) -> None:
        self.roles[membership.principal_id] = membership

    def remove_organization_membership(
        self, _organization_id: PublicId, principal_id: PublicId
    ) -> None:
        self.roles.pop(principal_id, None)

    def append_audit(self, event: AuditEvent) -> None:
        self.audits.append(event)

    def commit(self) -> None:
        return None


def _store() -> tuple[OrganizationMembershipStore, Principal, Principal, Principal]:
    organization = Organization.create("Research")
    admin = Principal.create(PrincipalKind.HUMAN, "Admin")
    researcher = Principal.create(PrincipalKind.HUMAN, "Researcher")
    machine = Principal.create(PrincipalKind.MACHINE, "Automation")
    return (
        OrganizationMembershipStore(
            organization,
            {value.id: value for value in (admin, researcher, machine)},
            {
                admin.id: OrganizationMembership(
                    organization.id, admin.id, OrganizationRole.ADMIN, NOW
                )
            },
        ),
        admin,
        researcher,
        machine,
    )


def test_admin_adds_audited_organization_member() -> None:
    store, admin, researcher, _machine = _store()

    result = OrganizationMembershipService(store).set(
        admin.id,
        store.organization.id,
        researcher.id,
        OrganizationRole.MEMBER,
        PublicId.generate(ResourceKind.REQUEST),
        NOW,
    )

    assert result.role is OrganizationRole.MEMBER
    assert store.audits[0].action == "organization_membership.set"
    assert store.audits[0].safe_metadata["organization_id"] == str(store.organization.id)


def test_last_admin_is_protected_and_machine_cannot_be_admin() -> None:
    store, admin, _researcher, machine = _store()
    service = OrganizationMembershipService(store)
    request = PublicId.generate(ResourceKind.REQUEST)

    with pytest.raises(ResourceConflict):
        service.set(
            admin.id,
            store.organization.id,
            admin.id,
            OrganizationRole.MEMBER,
            request,
            NOW,
        )
    with pytest.raises(ResourceConflict):
        service.remove(admin.id, store.organization.id, admin.id, request, NOW)
    with pytest.raises(ValueError, match="machine"):
        service.set(
            admin.id,
            store.organization.id,
            machine.id,
            OrganizationRole.ADMIN,
            request,
            NOW,
        )


def test_non_admin_cannot_read_or_change_organization_directory() -> None:
    store, admin, researcher, _machine = _store()
    service = OrganizationMembershipService(store)

    with pytest.raises(AuthorizationDenied):
        service.list(researcher.id, store.organization.id)
    with pytest.raises(AuthorizationDenied):
        service.set(
            researcher.id,
            store.organization.id,
            admin.id,
            OrganizationRole.MEMBER,
            PublicId.generate(ResourceKind.REQUEST),
            NOW,
        )
